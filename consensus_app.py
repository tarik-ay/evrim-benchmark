"""
Konsensus — Canli Fatura Cikarim Araci
======================================
Kullanici fatura yukler → 4 motor calisir → her baslik alaninda motorlarin
uzlasmasi ve cogunluk degeri gosterilir. Ground truth YOK.

Calistir:  streamlit run consensus_app.py
"""

import base64
import traceback

import pandas as pd
import streamlit as st

import consensus_core as cc


st.set_page_config(page_title="Konsensus — Fatura Cikarim", page_icon="🔎", layout="wide")

# ── config from secrets ──
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
    },
    "Nanonets": {
        "api_key": _sec("nanonets.api_key"),
        "model_id": _sec("nanonets.model_id"),
    },
    "Gemini": {
        "api_key": _sec("gemini.api_key"),
        "model": _sec("gemini.model", "gemini-2.0-flash"),
    },
}

def engine_ready(name):
    c = ENGINE_CFG[name]
    if name == "Rierino":  return bool(c.get("username") and c.get("password"))
    if name == "Claude":   return bool(c.get("api_key"))
    if name == "Nanonets": return bool(c.get("api_key") and c.get("model_id"))
    if name == "Gemini":   return bool(c.get("api_key"))
    return False


st.title("Konsensus — Fatura Cikarim")
st.caption("Ayni fatura 4 motora verilir; her alanda motorlarin ne kadar uzlastigi "
           "ve cogunlugun degeri gosterilir. Dogru cevap (ground truth) gerekmez.")

with st.sidebar:
    st.header("Girdi")
    pdf_file = st.file_uploader("Fatura PDF", type=["pdf"])

    st.header("Motorlar")
    chosen = []
    for name in ["Rierino", "Claude", "Nanonets", "Gemini"]:
        ready = engine_ready(name)
        label = name if ready else f"{name} — anahtar eksik"
        if st.checkbox(label, value=ready, disabled=not ready, key=f"e_{name}"):
            chosen.append(name)

    run = st.button("Calistir", type="primary", use_container_width=True,
                    disabled=not (pdf_file and len(chosen) >= 2))
    if pdf_file and len(chosen) < 2:
        st.info("Konsensus icin en az 2 motor gerekir.")
    st.divider()
    st.caption("Uzlasma = motorlarin hemfikir olma orani. Yuksek uzlasma guven "
               "sinyalidir, dogruluk garantisi degildir. Ayrisan motor 'hatali' "
               "degil, 'dogrulanmali' demektir.")


def show_pdf(pdf_bytes, height=820):
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}" width="100%" '
        f'height="{height}" style="border:1px solid #D8DED7;border-radius:6px;"></iframe>',
        unsafe_allow_html=True,
    )

STATUS_TR = {"tam": "tam uzlasma", "cogunluk": "cogunluk", "boluk": "bolunmus"}

def agreement_table(rows, engines):
    """One row per field; a column per engine + agreement + suggested + status."""
    out = []
    for r in rows:
        rec = {"Alan": r["label"]}
        for e in engines:
            rec[e] = r["values"].get(e, "—") if r["values"].get(e) is not None else "—"
        rec["Uzlasma"] = f'{round(r["agreement"]*100)}% ({len(r["top_group"])}/{r["n"]})'
        rec["Onerilen"] = r["suggested"] if r["has_majority"] else "—"
        rec["Durum"] = STATUS_TR.get(r["status"], r["status"])
        out.append(rec)
    return pd.DataFrame(out)

def style_table(df, engines):
    def color_status(v):
        if v == "tam uzlasma": return "color:#0F6E56;font-weight:600"
        if v == "cogunluk":    return "color:#854F0B;font-weight:600"
        if v == "bolunmus":    return "color:#A32D2D;font-weight:600"
        return ""
    return df.style.applymap(color_status, subset=["Durum"])


if run:
    pdf_bytes = pdf_file.read()
    cfgs = {name: ENGINE_CFG[name] for name in chosen}

    prog = st.progress(0.0, text="Motorlar calisiyor…")
    try:
        outputs = {}
        for i, name in enumerate(chosen):
            prog.progress(i / len(chosen), text=f"{name} calisiyor…")
            try:
                outputs[name] = cc.run_engine(name, pdf_bytes, ENGINE_CFG[name])
            except Exception as e:
                outputs[name] = {"error": f"{type(e).__name__}: {e}"}
        prog.progress(1.0, text="Bitti.")
        prog.empty()

        ok = {k: v for k, v in outputs.items() if not v.get("error")}
        bad = {k: v for k, v in outputs.items() if v.get("error")}

        if len(ok) < 2:
            st.error("Konsensus icin en az 2 motorun basarili calismasi gerekiyor.")
            for name, r in bad.items():
                st.warning(f"{name}: {r['error']}")
            st.stop()

        result = cc.consensus_header(ok)
        engines = list(ok.keys())

        left, right = st.columns([1, 1], gap="medium")

        with left:
            st.subheader("Yuklenen fatura")
            show_pdf(pdf_bytes)

        with right:
            m1, m2, m3 = st.columns(3)
            m1.metric("Genel uzlasma", f'{round(result["overall_agreement"]*100)}%')
            m2.metric("Tam uzlasan alan", result["clean"])
            m3.metric("Incelenecek", result["review"])

            if bad:
                st.warning("Calismayan motor: " + ", ".join(
                    f"{k} ({v['error'].split(':')[0]})" for k, v in bad.items()))

            st.caption(f"{len(engines)} motor: " + " · ".join(engines))

            df = agreement_table(result["rows"], engines)
            st.dataframe(style_table(df, engines),
                         use_container_width=True, hide_index=True, height=560)

            with st.expander("Incelenecek alanlar (uzlasma < %100)"):
                flagged = [r for r in result["rows"] if r["status"] != "tam"]
                if not flagged:
                    st.success("Tum alanlarda tam uzlasma.")
                for r in flagged:
                    vals = " · ".join(f"{e}: {v}" for e, v in r["values"].items())
                    miss = f" · okumadi: {', '.join(r['missing'])}" if r["missing"] else ""
                    st.markdown(
                        f"**{r['label']}** — uzlasma {round(r['agreement']*100)}% "
                        f"({len(r['top_group'])}/{r['n']}){miss}  \n{vals}"
                    )

        with st.expander("Ham motor ciktilari (baslik)"):
            st.json({name: (ok[name].get("header") or {}) for name in engines})

    except Exception as e:
        prog.empty()
        st.error(f"Hata: {type(e).__name__}: {e}")
        with st.expander("Ayrinti"):
            st.code(traceback.format_exc(), language="text")

else:
    st.info("Soldan bir fatura yukleyip motorlari secin, sonra **Calistir**.")
    st.markdown(
        "- Her alan icin 4 motorun degeri yan yana gorunur\n"
        "- **Uzlasma %** = en kalabalik ayni-deger grubu / o alani okuyan motor sayisi\n"
        "- **Onerilen** = cogunlugun degeri (net cogunluk varsa)\n"
        "- **Durum** = tam uzlasma / cogunluk / bolunmus\n\n"
        "Ground truth yok — referans, motorlarin birbiriyle uzlasmasi."
    )
