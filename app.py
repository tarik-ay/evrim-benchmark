"""
Evrim — Engine Benchmark (Rierino · Claude · Nanonets · Gemini)
Upload a PDF + its ground-truth JSON, pick engines, get scored results.
Run:  streamlit run app.py
Keys/config live in .streamlit/secrets.toml (never in code).
"""

import json
import time
import traceback

import pandas as pd
import streamlit as st

import benchmark_core as bc


st.set_page_config(page_title="Evrim Engine Benchmark", page_icon="📄", layout="wide")

# ─────────────────────────────────────────────────────────────
# Config from secrets (with safe fallbacks so the app still loads)
# ─────────────────────────────────────────────────────────────
def _sec(path, default=None):
    cur = st.secrets
    try:
        for k in path.split("."):
            cur = cur[k]
        return cur
    except Exception:
        return default

ENGINE_CFG = {
    "Rierino": {
        "base_url": _sec("rierino.base_url", "http://16.171.20.18:8080"),
        "username": _sec("rierino.username"),
        "password": _sec("rierino.password"),
    },
    "Claude": {
        "api_key": _sec("anthropic.api_key"),
        "model": _sec("anthropic.model", "claude-sonnet-5"),
        "price_in_per_mtok": _sec("anthropic.price_in_per_mtok", 3.0),
        "price_out_per_mtok": _sec("anthropic.price_out_per_mtok", 15.0),
    },
    "Nanonets": {
        "api_key": _sec("nanonets.api_key"),
        "model_id": _sec("nanonets.model_id"),
        "price_per_page": _sec("nanonets.price_per_page", 0.0),
    },
    "Gemini": {
        "api_key": _sec("gemini.api_key"),
        "model": _sec("gemini.model", "gemini-2.5-flash"),
        "price_in_per_mtok": _sec("gemini.price_in_per_mtok", 0.30),
        "price_out_per_mtok": _sec("gemini.price_out_per_mtok", 2.50),
    },
}

def engine_ready(name):
    c = ENGINE_CFG[name]
    if name == "Rierino":
        return bool(c.get("username") and c.get("password"))
    if name == "Claude":
        return bool(c.get("api_key"))
    if name == "Nanonets":
        return bool(c.get("api_key") and c.get("model_id"))
    if name == "Gemini":
        return bool(c.get("api_key"))
    return False


# ─────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────
st.title("Fatura Çıkarım Motoru Benchmark")
st.caption("Rierino · Claude · Nanonets · Gemini — ortak şemaya çevirir, ground truth ile puanlar.")

with st.sidebar:
    st.header("1 · Girdi")
    pdf_file = st.file_uploader("Fatura PDF", type=["pdf"])
    gt_file = st.file_uploader("Ground truth (JSON)", type=["json"])

    st.header("2 · Motorlar")
    chosen = []
    for name in ["Rierino", "Claude", "Nanonets", "Gemini"]:
        ready = engine_ready(name)
        label = name if ready else f"{name} — anahtar/uç nokta eksik"
        if st.checkbox(label, value=ready, disabled=not ready, key=f"eng_{name}"):
            chosen.append(name)

    st.header("3 · Çalıştır")
    run = st.button("Puanla", type="primary", use_container_width=True,
                    disabled=not (pdf_file and gt_file and chosen))
    if not (pdf_file and gt_file):
        st.info("PDF ve ground truth yükleyin.")
    elif not chosen:
        st.info("En az bir motor seçin.")


# ─────────────────────────────────────────────────────────────
# Helpers for display
# ─────────────────────────────────────────────────────────────
def pct(x): return f"{x*100:.1f}%"


def field_table(section: dict) -> pd.DataFrame:
    rows = []
    for fld, d in section.items():
        rows.append({
            "Field": fld,
            "Ground truth": d["ground_truth"],
            "Extracted": d["extracted"],
            "Match": "✓" if d["matched"] else "✗",
        })
    return pd.DataFrame(rows)


def style_match(df):
    def color(v):
        if v == "✓":
            return "color:#0E6B4F;font-weight:600"
        if v == "✗":
            return "color:#B84A3A;font-weight:600"
        return ""
    return df.style.applymap(color, subset=["Match"])


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────
if run:
    pdf_bytes = pdf_file.read()
    try:
        gt = json.loads(gt_file.read().decode("utf-8"))
    except Exception as e:
        st.error(f"Ground truth JSON okunamadı: {e}")
        st.stop()

    if "items" not in gt:
        st.warning("Ground truth içinde 'items' listesi yok — sadece header puanlanacak.")

    results = {}
    prog = st.progress(0.0, text="Motorlar çalışıyor…")
    for i, name in enumerate(chosen):
        prog.progress(i / len(chosen), text=f"{name} çalışıyor…")
        try:
            out = bc.run_engine(name, pdf_bytes, ENGINE_CFG[name])
            metrics = bc.calculate_metrics(out["header"], out["items"], gt)
            results[name] = {"out": out, "metrics": metrics, "error": None}
        except Exception as e:
            results[name] = {"error": f"{type(e).__name__}: {e}",
                             "trace": traceback.format_exc()}
    prog.progress(1.0, text="Bitti.")
    time.sleep(0.2)
    prog.empty()

    ok = {k: v for k, v in results.items() if not v.get("error")}
    bad = {k: v for k, v in results.items() if v.get("error")}

    # ── Comparison summary ──
    if ok:
        st.subheader("Karşılaştırma")
        comp = []
        for name, r in ok.items():
            s = r["metrics"]["summary"]
            comp.append({
                "Motor": name,
                "Genel": pct(s["overall_accuracy"]),
                "Header": f'{pct(s["header_accuracy"])} ({s["header_matched"]}/{s["header_total"]})',
                "Satır": f'{pct(s["item_accuracy"])} ({s["item_matched"]}/{s["item_total"]})',
                "Eşleşen/Toplam": f'{s["total_matched"]}/{s["total_fields"]}',
                "Süre": f'{r["out"]["latency"]:.1f}s',
                "Maliyet": f'${r["out"]["cost"]:.4f}',
                "Fazla satır": r["metrics"].get("extra_lines", 0),
            })
        comp_df = pd.DataFrame(comp).sort_values("Genel", ascending=False)
        st.dataframe(comp_df, use_container_width=True, hide_index=True)

        # metric cards for the leader
        best = max(ok.items(), key=lambda kv: kv[1]["metrics"]["summary"]["overall_accuracy"])
        st.caption(f"En yüksek genel doğruluk: **{best[0]}**")

    if bad:
        st.subheader("Hata veren motorlar")
        for name, r in bad.items():
            with st.expander(f"⚠ {name}: {r['error']}"):
                st.code(r.get("trace", ""), language="text")

    # ── Per-engine detail ──
    if ok:
        st.subheader("Motor Detayları")
        tabs = st.tabs(list(ok.keys()))
        for tab, (name, r) in zip(tabs, ok.items()):
            with tab:
                s = r["metrics"]["summary"]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Genel doğruluk", pct(s["overall_accuracy"]))
                c2.metric("Header", pct(s["header_accuracy"]))
                c3.metric("Satır", pct(s["item_accuracy"]))
                c4.metric("Süre", f'{r["out"]["latency"]:.1f}s')

                st.markdown("**Header alanları**")
                if r["metrics"]["header"]:
                    st.dataframe(style_match(field_table(r["metrics"]["header"])),
                                 use_container_width=True, hide_index=True)
                else:
                    st.caption("Puanlanan header alanı yok.")

                st.markdown("**Satır alanları**")
                for idx, item in enumerate(r["metrics"]["items"], 1):
                    if not item["fields"]:
                        continue
                    with st.expander(f"Satır {idx} — {item['matched']}/{item['total']} eşleşti"):
                        st.dataframe(style_match(field_table(item["fields"])),
                                     use_container_width=True, hide_index=True)

                if r["out"].get("ocr_text"):
                    with st.expander("OCR metni (parser çıktısı)"):
                        st.text(r["out"]["ocr_text"][:5000])

                with st.expander("Ham motor çıktısı (JSON)"):
                    st.json({"header": r["out"]["header"], "items": r["out"]["items"]})

    # ── Download combined report ──
    if ok:
        report = {name: {"summary": r["metrics"]["summary"],
                         "latency": r["out"]["latency"], "cost": r["out"]["cost"]}
                  for name, r in ok.items()}
        st.download_button("Raporu indir (JSON)",
                           data=json.dumps(report, ensure_ascii=False, indent=2),
                           file_name="benchmark_report.json", mime="application/json")

else:
    st.info("Soldan PDF + ground truth yükleyip motor seçin, sonra **Puanla**.")
    with st.expander("Ground truth formatı"):
        st.code(json.dumps({
            "Total_Weight_Gross": 523, "currency": "EUR", "deliveryMethod": "CPT",
            "carryingMethod": "ROAD", "tradeCountryCode": "CZ", "totalAmount": 13367.08,
            "items": [{
                "invoiceNo": "36531622", "invoiceDate": "2025-11-27",
                "productCode": "2238019-1", "description": "VAL-U-LOK SKT BR SN 22-26AWG",
                "gtip": "8536699099", "countryOfOriginCode": "CN",
                "quantity": 120000, "quantityUnitCode": "PCE",
                "netWeight": 35.2, "amount": 909.6, "purchaseOrder": "GBBSTYC08251"
            }]
        }, ensure_ascii=False, indent=2), language="json")
