"""
Consensus Core — canli arac (ground truth YOK)
==============================================
Ayni faturayi 4 motora verir, her BASLIK alaninda motorlarin birbirleriyle ne
kadar uzlastigini olcer. Ground truth yok — referans, motorlarin kendisi.

Iki gorunum:
  • Option 1 — Uzlasma %: alandaki en kalabalik "ayni deger" grubu / deger veren motor sayisi
  • Option 2 — Cogunluk oyu: en kalabalik grubun degeri "onerilen", azinlik "isaretli"

Karsilastirma NORMALIZE edilmis degerler uzerinden (values_match) — format farki
(1.250,50 vs 1250.50 / KG vs KGM) sahte ayrisma uretmesin.

DURUSTLUK KURALLARI (aracta gosterilir):
  • Yuksek uzlasma = guven sinyali, dogruluk GARANTISI degil (hepsi ayni hatayi yapabilir).
  • Outlier = "farkli, dogrulanmali" — "hatali" DEGIL.

Bu ilk surum sadece BASLIK alanlarini karsilastirir (satir bazli konsensus faz 2).
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from benchmark_core import HEADER_FIELDS, values_match, run_engine

# Baslik alanlarinin okunur Turkce adlari
FIELD_LABELS = {
    "supplierName": "Satici adi", "supplierAddress": "Satici adres", "supplierVAT": "Satici VN",
    "buyerName": "Alici adi", "buyerAddress": "Alici adres", "buyerVAT": "Alici VN",
    "deliveryMethod": "Teslim sekli", "carryingMethod": "Tasima sekli",
    "dischargeCustoms": "Gumruk", "tradeCountryCode": "Ticaret ulke kodu",
    "currency": "Doviz", "totalAmount": "Toplam tutar",
    "Total_Weight_Gross": "Toplam brut agirlik", "Total_Weight_Net": "Toplam net agirlik",
    "freight": "Navlun", "insurance": "Sigorta", "bank": "Banka",
    "paymentMethod": "Odeme sekli", "termOfPayment": "Odeme vadesi",
    "documentType": "Belge tipi", "consignmentNo": "Konsimento no",
    "discount": "Iskonto",
}

# Master-data — faturada yok, konsensus disi
SKIP_FIELDS = {"buyerCode", "buyerVKN", "supplierCode", "supplierVKN",
               "freightCurrency", "insuranceCurrency", "discount"}


def _norm_display(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None


def _cluster_field(values: Dict[str, Any]) -> List[List[str]]:
    """
    values: {engine_name: value}  (yalnizca deger VEREN motorlar)
    Ayni degeri veren motorlari grupla (values_match ile, normalize).
    Doner: gruplar listesi, her grup motor adlari listesi, buyukten kucuge.
    """
    engines = [e for e, v in values.items() if _norm_display(v) is not None]
    groups: List[List[str]] = []
    reps: List[Any] = []
    for e in engines:
        v = values[e]
        placed = False
        for gi, rep in enumerate(reps):
            if values_match(v, rep) or values_match(rep, v):
                groups[gi].append(e)
                placed = True
                break
        if not placed:
            reps.append(v)
            groups.append([e])
    order = sorted(range(len(groups)), key=lambda i: len(groups[i]), reverse=True)
    return [groups[i] for i in order], [reps[i] for i in order], engines


def consensus_header(engine_outputs: Dict[str, dict]) -> dict:
    """
    engine_outputs: {engine_name: {"header": {...}, ...}}
    Her baslik alani icin uzlasma % + cogunluk degeri + outlier motorlar.
    """
    rows = []
    agree_sum = agree_n = 0

    for field in HEADER_FIELDS:
        if field in SKIP_FIELDS:
            continue
        # her motorun bu alandaki degeri
        values = {}
        for eng, out in engine_outputs.items():
            values[eng] = (out.get("header") or {}).get(field)

        groups, reps, contributing = _cluster_field(values)
        n = len(contributing)  # deger veren motor sayisi

        if n == 0:
            # hicbir motor okumamis — atla (gosterme)
            continue

        top = groups[0]
        top_val = reps[0]
        agreement = len(top) / n           # 0..1
        majority_val = top_val if len(top) * 2 > n else None  # gercek cogunluk mu?
        outliers = [e for g in groups[1:] for e in g]

        # durum
        if agreement == 1.0:
            status = "tam"          # hepsi ayni
        elif majority_val is not None:
            status = "cogunluk"     # cogunluk var, azinlik isaretli
        else:
            status = "boluk"        # bolunmus, net cogunluk yok — insan baksin

        rows.append({
            "field": field,
            "label": FIELD_LABELS.get(field, field),
            "values": {e: _norm_display(values[e]) for e in contributing},
            "missing": [e for e in engine_outputs if e not in contributing],
            "agreement": agreement,
            "n": n,
            "top_group": top,
            "suggested": _norm_display(top_val),
            "has_majority": majority_val is not None,
            "outliers": outliers,
            "status": status,
        })
        agree_sum += agreement
        agree_n += 1

    overall = (agree_sum / agree_n) if agree_n else 0.0
    clean = sum(1 for r in rows if r["status"] == "tam")
    review = sum(1 for r in rows if r["status"] in ("cogunluk", "boluk"))

    return {
        "overall_agreement": overall,
        "field_count": agree_n,
        "clean": clean,
        "review": review,
        "rows": rows,
    }


def run_all_engines(pdf_bytes: bytes, engine_cfgs: Dict[str, dict]) -> Dict[str, dict]:
    """Her motoru calistir, {engine: {"header","items","latency","error"}} doner."""
    results = {}
    for name, cfg in engine_cfgs.items():
        try:
            out = run_engine(name, pdf_bytes, cfg)
            results[name] = out
        except Exception as e:
            results[name] = {"error": f"{type(e).__name__}: {e}"}
    return results
