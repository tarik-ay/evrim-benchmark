"""
Consensus Core — canli arac (ground truth YOK)
==============================================
Ayni faturayi 4 motora verir, her alanda (baslik VE satir) motorlarin birbirleriyle
ne kadar uzlastigini olcer. Ground truth yok — referans, motorlarin kendisi.

Iki gorunum:
  • Option 1 — Uzlasma %: alandaki en kalabalik "ayni deger" grubu / deger veren motor sayisi
  • Option 2 — Cogunluk oyu: en kalabalik grubun degeri "onerilen", azinlik "isaretli"

Karsilastirma NORMALIZE edilmis degerler uzerinden (values_match) — format farki
(1.250,50 vs 1250.50 / KG vs KGM) sahte ayrisma uretmesin.

DURUSTLUK KURALLARI (aracta gosterilir):
  • Yuksek uzlasma = guven sinyali, dogruluk GARANTISI degil (hepsi ayni hatayi yapabilir).
  • Outlier = "farkli, dogrulanmali" — "hatali" DEGIL.
  • Bir alani sadece 1 motor okumussa bu "tam uzlasma" DEGIL ("tek motor") — uzlasacak
    baska deger yok, karsilastirma yapilamadi anlamina gelir.

Satir (line item) konsensusu icin ground truth yok, yani motorlari BIRBIRINE hizalamak
gerekiyor (N-yonlu). Yontem: her motor ciftinden pairwise skorla eslesen kalemleri
union-find ile ayni "kanonik kalem grubu"na topla, sonra grup icinde tutarlilik kontrolu
yap (transitif surukleme / bolunmus-birlesmis kalem riskine karsi).
"""

from __future__ import annotations
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from benchmark_core import (
    HEADER_FIELDS, TABLE_FIELDS, values_match, run_engine,
    _norm_code, _num_dist, _to_str,
)

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
    "discount": "Iskonto", "invoiceNo": "Fatura no", "invoiceDate": "Fatura tarihi",
}

# Master-data — faturada yok, konsensus disi
SKIP_FIELDS = {"buyerCode", "buyerVKN", "supplierCode", "supplierVKN",
               "freightCurrency", "insuranceCurrency", "discount"}

# invoiceNo/invoiceDate faturanin tamami icin TEK bir degerdir ama semada her
# satira kopyalanir (eski GT araci icin). Satir konsensusunde tekrar tekrar
# (her kalemde ayni deger) gostermek gurultu — bunlar artik HEADER_FIELDS'te,
# bir kez orada karsilastiriliyor. Satir gorunumunde atla.
LINE_SKIP_FIELDS = {"invoiceNo", "invoiceDate"}

# Satir (line item) alanlarinin okunur Turkce adlari
TABLE_FIELD_LABELS = {
    "amount": "Tutar", "countryOfOriginCode": "Mense ulke", "currency": "Doviz",
    "description": "Aciklama", "discountItem": "Iskonto (kalem)",
    "grossWeight": "Brut agirlik", "gtip": "GTIP", "invoiceDate": "Fatura tarihi",
    "invoiceNo": "Fatura no", "netWeight": "Net agirlik", "productCode": "Urun kodu",
    "purchaseOrder": "Siparis no", "quantity": "Miktar",
    "quantityUnitCode": "Miktar birimi", "unitPrice": "Birim fiyat",
    "weightUnitCode": "Agirlik birimi",
}


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


def _field_consensus(values: Dict[str, Any]) -> Optional[dict]:
    """
    values: {engine: raw_value} — TEK bir alan icin, tum motorlarin degeri.
    Deger veren motor yoksa None. Varsa uzlasma kaydi doner (field/label eklenmeden,
    cagiran ekler — hem baslik hem satir alanlari bunu paylasir).
    """
    groups, reps, contributing = _cluster_field(values)
    n = len(contributing)
    if n == 0:
        return None

    top = groups[0]
    top_val = reps[0]
    agreement = len(top) / n
    majority_val = top_val if len(top) * 2 > n else None
    outliers = [e for g in groups[1:] for e in g]

    if n == 1:
        # Tek motor okumus — uzlasacak baska deger yok; "tam uzlasma" DEGIL.
        status = "tek"
    elif agreement == 1.0:
        status = "tam"          # hepsi ayni
    elif majority_val is not None:
        status = "cogunluk"     # cogunluk var, azinlik isaretli
    else:
        status = "boluk"        # bolunmus, net cogunluk yok — insan baksin

    return {
        "values": {e: _norm_display(values[e]) for e in contributing},
        "contributing": contributing,
        "agreement": agreement,
        "n": n,
        "top_group": top,
        "suggested": _norm_display(top_val),
        "has_majority": majority_val is not None,
        "outliers": outliers,
        "status": status,
    }


def consensus_header(engine_outputs: Dict[str, dict]) -> dict:
    """
    engine_outputs: {engine_name: {"header": {...}, ...}}
    Her baslik alani icin uzlasma % + cogunluk degeri + outlier motorlar.
    """
    rows = []
    agree_sum = agree_n = 0   # sadece n>=2 (gercekten karsilastirilan) alanlar

    for field in HEADER_FIELDS:
        if field in SKIP_FIELDS:
            continue
        values = {eng: (out.get("header") or {}).get(field) for eng, out in engine_outputs.items()}
        rec = _field_consensus(values)
        if rec is None:
            continue   # hicbir motor okumamis — gosterme

        rec["field"] = field
        rec["label"] = FIELD_LABELS.get(field, field)
        rec["missing"] = [e for e in engine_outputs if e not in rec["contributing"]]
        rows.append(rec)
        # "tek" (n==1) alani ortalamaya katma: karsilastirma yok, uzlasma da yok —
        # dahil edilirse "genel uzlasma" sahte sekilde sisirilir.
        if rec["status"] != "tek":
            agree_sum += rec["agreement"]
            agree_n += 1

    overall = (agree_sum / agree_n) if agree_n else 0.0
    clean = sum(1 for r in rows if r["status"] == "tam")
    review = sum(1 for r in rows if r["status"] in ("cogunluk", "boluk"))
    single = sum(1 for r in rows if r["status"] == "tek")

    return {
        "overall_agreement": overall,
        "field_count": len(rows),
        "compared_field_count": agree_n,
        "clean": clean,
        "review": review,
        "single_engine_only": single,
        "rows": rows,
    }


# ═══════════════════════════════════════════════════════════════════════
# SATIR (LINE ITEM) HIZALAMA — ground truth yok, motorlari BIRBIRINE hizala
# ═══════════════════════════════════════════════════════════════════════

# GT'ye karsi degil, motor-motor karsilastirmaya gore; gercek faturalarda
# kalibre edilmeli (bkz. HANDOVER) — sabit "dogru" deger degil, baslangic noktasi.
PAIR_MATCH_THRESHOLD = 0.55
DRIFT_SANITY_THRESHOLD = 0.4   # transitif surukleme koruma esigi


def _closeness(d: float) -> float:
    """0..1 dist -> 1..0 continuous closeness (inf -> 0)."""
    return 0.0 if d == float("inf") else max(0.0, 1.0 - min(d, 1.0))


def _code_match(a: dict, b: dict) -> bool:
    """
    Gercek faturada dogrulandi: motorlar productCode/description alanlarini FARKLI
    kolonlara esleyebilir (ayni fatura, Nanonets productCode<->description'i Claude/
    Gemini'ye gore ters kullandi — tutar/miktar birebir ayniydi, sadece hangi alanin
    "kod" oldugu konusunda ayristi). Ground truth olmadigi icin hangisinin "dogru"
    oldugunu soyleyemeyiz; bu yuzden bir motorun productCode'unu digerinin HEM
    productCode'una HEM description'ina karsi kontrol ederiz (ve tersi).
    """
    a_codes = {c for c in (_norm_code(a.get("productCode")), _norm_code(a.get("description"))) if c}
    b_codes = {c for c in (_norm_code(b.get("productCode")), _norm_code(b.get("description"))) if c}
    return bool(a_codes & b_codes)


def _pair_score(a: dict, b: dict) -> float:
    """
    Iki motorun TEK kalemi arasinda simetrik benzerlik (GT yok, iki tarafli).
    Kod eslesmesi guclu sinyaldir ama TEK BASINA yeterli degil: ayni kod birden
    fazla satirda tekrarlaniyorsa (duplicate), hangi motorun hangi satiri hangisiyle
    esledigini tutar/miktar yakinligi belirlemeli — yoksa tum ayni-kodlu adaylar
    esit (1.0) skorlanir ve eslesme rastgele siraya dusebilir.
    """
    code_match = _code_match(a, b)

    score = 0.0
    ad, bd = _to_str(a.get("description")), _to_str(b.get("description"))
    if ad and bd:
        score += 0.5 * SequenceMatcher(None, ad, bd).ratio()
    agtip, bgtip = _norm_code(a.get("gtip")), _norm_code(b.get("gtip"))
    if agtip and bgtip and agtip == bgtip:
        score += 0.25
    amt_close = _closeness(_num_dist(a.get("amount"), b.get("amount")))
    qty_close = _closeness(_num_dist(a.get("quantity"), b.get("quantity")))
    score += 0.15 * amt_close + 0.10 * qty_close

    if code_match:
        # Kod eslesen adaylari 0.9 tabanina tasi, tutar/miktar yakinligiyla
        # ince-ayarla — duplicate kodlarda dogru satiri secmek icin gerekli.
        score = max(score, 0.9) + 0.05 * amt_close + 0.05 * qty_close
    return score


def _match_pair(items_a: List[dict], items_b: List[dict]) -> List[Tuple[int, int, float]]:
    """Iki motorun kalem listeleri arasinda greedy en-iyi-once bipartite eslesme."""
    cands = []
    for i, a in enumerate(items_a):
        for j, b in enumerate(items_b):
            s = _pair_score(a, b)
            if s >= PAIR_MATCH_THRESHOLD:
                cands.append((s, i, j))
    cands.sort(key=lambda x: -x[0])
    used_a, used_b, matched = set(), set(), []
    for s, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matched.append((i, j, s))
    return matched


class _UnionFind:
    def __init__(self):
        self.parent: Dict[Any, Any] = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def _split_drifted_members(by_engine: Dict[str, dict]) -> Tuple[Dict[str, dict], List[Tuple[str, dict]]]:
    """
    Union-find transitif oldugu icin A-B ve B-C eslesirse A-C hic karsilastirilmadan
    ayni gruba dusebilir. >2 uyeli gruplarda her uyenin digerlerine en yakin skorunu
    kontrol et; hicbirine yeterince yakin degilse grup disina cikar (kendi tek-kisilik
    grubuna duser, "kontrol edilmeli" olarak isaretlenir).
    """
    if len(by_engine) <= 2:
        return by_engine, []
    items = list(by_engine.items())
    kept, drifted = dict(items), []
    for e, item in items:
        others = [oi for oe, oi in items if oe != e]
        best = max((_pair_score(item, oi) for oi in others), default=0.0)
        if best < DRIFT_SANITY_THRESHOLD:
            drifted.append((e, item))
    for e, _ in drifted:
        del kept[e]
    return kept, drifted


def build_canonical_groups(engine_items: Dict[str, List[dict]]) -> Tuple[List[Dict[str, dict]], List[dict]]:
    """
    engine_items: {engine_name: [item, ...]}
    Doner: (gruplar, conflicts)
      gruplar   — her biri {engine_name: item} (o gruptaki "ayni urun" kalemi)
      conflicts — insan incelemesi gereken durumlar (ayni motorun bir grupta 2 adayi
                  cikmasi, ya da transitif surukleme nedeniyle gruptan cikarilan uye)
    """
    engines = list(engine_items.keys())
    uf = _UnionFind()
    for e in engines:
        for i in range(len(engine_items[e])):
            uf.find((e, i))

    for a in range(len(engines)):
        for b in range(a + 1, len(engines)):
            ea, eb = engines[a], engines[b]
            for i, j, _ in _match_pair(engine_items[ea], engine_items[eb]):
                uf.union((ea, i), (eb, j))

    comps: Dict[Any, List[Tuple[str, int]]] = {}
    for e in engines:
        for i in range(len(engine_items[e])):
            comps.setdefault(uf.find((e, i)), []).append((e, i))

    groups, conflicts = [], []
    for members in comps.values():
        by_engine_cands: Dict[str, List[dict]] = {}
        for e, i in members:
            by_engine_cands.setdefault(e, []).append(engine_items[e][i])

        by_engine: Dict[str, dict] = {}
        dupes: List[Tuple[str, dict]] = []
        for e, cands in by_engine_cands.items():
            if len(cands) == 1:
                by_engine[e] = cands[0]
                continue
            # Ayni motorun bu grupta birden fazla adayi var (split/merge kaynakli
            # olasi cakisma) — digerlerine en yakin toplam skoru olani grupta tut.
            others = [(oe, oc) for oe, ocs in by_engine_cands.items() if oe != e for oc in ocs]

            def total_score(c, _others=others):
                return sum(_pair_score(c, oc) for _, oc in _others)

            best = max(cands, key=total_score)
            by_engine[e] = best
            dupes.extend((e, c) for c in cands if c is not best)

        kept, drifted = _split_drifted_members(by_engine)
        groups.append(kept)
        extra = dupes + drifted
        if extra:
            conflicts.append({"group": kept, "extra": extra})
        for e, item in drifted:
            groups.append({e: item})
        for e, item in dupes:
            groups.append({e: item})

    return groups, conflicts


def consensus_lines(engine_outputs: Dict[str, dict]) -> dict:
    """
    engine_outputs: {engine_name: {"items": [...], ...}}
    Kalemleri motorlar arasi hizalar (ground truth yok), sonra her kanonik kalem
    grubunda her TABLE_FIELD icin uzlasma hesaplar (consensus_header ile ayni desen).
    """
    engines = list(engine_outputs.keys())
    engine_items = {e: (engine_outputs[e].get("items") or []) for e in engines}
    groups, conflicts = build_canonical_groups(engine_items)

    # Goruntuleme sirasi: en cok kalemi olan motorun kendi sirasina gore (stabil,
    # insan-okur sira) — eslesme mantigini etkilemez, sadece gosterim sirasidir.
    # id() ile pozisyon haritasi: value-esit (duplicate) satirlarda list.index()
    # yanlis (ilk eslesen) satiri donebilir, id() gercek konumu garantiler.
    pos = {e: {id(it): idx for idx, it in enumerate(engine_items[e])} for e in engines}
    anchor = max(engines, key=lambda e: len(engine_items[e])) if engines else None

    def sort_key(group: Dict[str, dict]) -> float:
        if anchor and anchor in group:
            return float(pos[anchor][id(group[anchor])])
        idxs = [pos[e][id(v)] for e, v in group.items()]
        return sum(idxs) / len(idxs) + 0.5   # yaklasik sirala, cakisirsa ondalikla ayir

    ordered = sorted(groups, key=sort_key)

    rows = []
    agree_sum = agree_n = 0
    for group in ordered:
        field_rows = []
        row_agree_sum = row_agree_n = 0
        for field in TABLE_FIELDS:
            if field in LINE_SKIP_FIELDS:
                continue
            values = {e: item.get(field) for e, item in group.items()}
            rec = _field_consensus(values)
            if rec is None:
                continue
            rec["field"] = field
            rec["label"] = TABLE_FIELD_LABELS.get(field, field)
            field_rows.append(rec)
            # "tek" (n==1) alani ortalamaya katma — bkz. consensus_header, ayni gerekce.
            if rec["status"] != "tek":
                row_agree_sum += rec["agreement"]
                row_agree_n += 1

        rows.append({
            "engines_present": list(group.keys()),
            "engines_missing": [e for e in engines if e not in group],
            "single_engine_only": len(group) == 1,
            "fields": field_rows,
            "row_agreement": (row_agree_sum / row_agree_n) if row_agree_n else 0.0,
        })
        agree_sum += row_agree_sum
        agree_n += row_agree_n

    for i, r in enumerate(rows, start=1):
        r["index"] = i

    overall = (agree_sum / agree_n) if agree_n else 0.0
    single_only = sum(1 for r in rows if r["single_engine_only"])

    return {
        "rows": rows,
        "overall_line_agreement": overall,
        "group_count": len(rows),
        "single_engine_only_count": single_only,
        "conflicts": conflicts,
    }


# ═══════════════════════════════════════════════════════════════════════
# MOTOR OZETI — tek fatura calistirmasindan turetilen, is-okur ozet
# ═══════════════════════════════════════════════════════════════════════

def engine_summary(header_result: dict, line_result: dict, engines: List[str]) -> List[dict]:
    """
    Bu TEK faturadan turetilir (kalici/coklu-fatura istatistik DEGIL — o ayri is,
    bkz. HANDOVER F maddesi). Her motor icin: kac alan cevapladi, bunlarin kacinda
    en kalabalik gruptaydi (cogunlukla hemfikir), kacinda tek basina ayristi.
    """
    stats = {e: {"answered": 0, "in_top_group": 0, "outlier": 0} for e in engines}

    def tally(rec: dict):
        for e in rec["contributing"]:
            if e not in stats:
                continue
            stats[e]["answered"] += 1
            if e in rec["top_group"]:
                stats[e]["in_top_group"] += 1
            else:
                stats[e]["outlier"] += 1

    for r in header_result["rows"]:
        tally(r)
    for row in line_result["rows"]:
        for r in row["fields"]:
            tally(r)

    total_opportunities = header_result["field_count"] + sum(
        len(row["fields"]) for row in line_result["rows"]
    )

    out = []
    for e in engines:
        s = stats[e]
        answered = s["answered"]
        out.append({
            "engine": e,
            "answered": answered,
            "coverage": (answered / total_opportunities) if total_opportunities else 0.0,
            "in_top_group": s["in_top_group"],
            "outlier": s["outlier"],
            "alignment_rate": (s["in_top_group"] / answered) if answered else None,
        })
    out.sort(key=lambda r: (r["alignment_rate"] if r["alignment_rate"] is not None else -1), reverse=True)
    return out


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
