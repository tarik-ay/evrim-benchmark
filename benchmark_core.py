"""
Evrim Fatura → Beyanname — Engine Benchmark Core
=================================================
One canonical schema. Every engine converts INTO it. Ground truth is in it too.
Then: align line items by product code -> score each engine vs ground truth.

Engines: Rierino (real endpoint), Claude (Anthropic API, reads PDF directly),
Nanonets (OCR API), Gemini (stub until key).

Nothing here is engine-to-engine. Everything meets at CANONICAL SCHEMA.
"""

from __future__ import annotations
import re, io, json, time, base64, uuid
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth


# ═══════════════════════════════════════════════════════════════════════
# 1. CANONICAL BENCHMARK SCHEMA  (the hub)
# ═══════════════════════════════════════════════════════════════════════

HEADER_FIELDS = [
    "Total_Weight_Gross", "Total_Weight_Net",
    "bank", "buyerCode", "buyerVKN",
    "carryingMethod", "consignmentNo", "currency",
    "deliveryMethod", "dischargeCustoms", "discount",
    "documentType", "freight", "freightCurrency",
    "insurance", "insuranceCurrency", "paymentMethod",
    "supplierCode", "supplierVKN", "termOfPayment",
    "totalAmount", "tradeCountryCode",
]

TABLE_FIELDS = [
    "amount", "countryOfOriginCode", "currency",
    "description", "discountItem", "grossWeight",
    "gtip", "invoiceDate", "invoiceNo", "netWeight",
    "productCode", "purchaseOrder", "quantity",
    "quantityUnitCode", "unitPrice", "weightUnitCode",
]

# Master-data fields: NOT on the invoice (come from EvrimX customer master).
# Excluded from invoice-extraction scoring for ALL engines equally.
MASTER_DATA_FIELDS = {"buyerCode", "buyerVKN", "supplierCode", "supplierVKN"}

FUZZY_MATCH_THRESHOLD = 0.85


# ═══════════════════════════════════════════════════════════════════════
# 2. VALUE NORMALIZATION & MATCHING   (ported & kept from EvrimX notebook)
# ═══════════════════════════════════════════════════════════════════════

def _to_float(v):
    try:
        s = str(v).strip()
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")     # EU: 1.000,00
            else:
                s = s.replace(",", "")                        # US: 1,000.00
        elif "," in s:
            s = s.replace(",", ".")
        elif s.count(".") > 1:
            parts = s.split(".")
            s = "".join(parts[:-1]) + "." + parts[-1]
        s = re.sub(r"[^\d.\-]", "", s)
        return float(s) if s not in ("", "-", ".") else None
    except Exception:
        return None


def _to_float_eu_thousands(v):
    """Nanonets EU edge case: '25.000' -> 25000, '1.234.567' -> 1234567."""
    try:
        s = re.sub(r"[^\d.\-]", "", str(v).strip())
        if "." not in s:
            return None
        if re.match(r"^\d{1,3}(\.\d{3})+$", s):
            return float(s.replace(".", ""))
        return None
    except Exception:
        return None


def _to_str(v):
    return str(v).strip().lower() if v is not None else ""


# Unit-code equivalence: keyers write loose units ("KG", "PC"), engines emit
# UN/CEFACT ("KGM", "PCE"). Treat members of the same group as equal.
_UNIT_GROUPS = [
    {"kgm", "kg", "kgs", "kilo", "kilogram", "kilograms"},
    {"pce", "pc", "pcs", "piece", "pieces", "ea", "each", "adet", "ad", "st"},
    {"mtr", "m", "metre", "meter", "meters"},
    {"ltr", "l", "lt", "litre", "liter", "liters"},
    {"mtq", "m3", "cbm"},
    {"tne", "ton", "tonne", "tons", "mt"},
]
_UNIT_CANON = {}
for _grp in _UNIT_GROUPS:
    for _u in _grp:
        _UNIT_CANON[_u] = min(_grp)   # any stable representative


def _unit_equiv(a, b) -> bool:
    """True if a and b are the same unit under UN/CEFACT <-> loose equivalence."""
    ca = _UNIT_CANON.get(_to_str(a).replace(".", ""))
    cb = _UNIT_CANON.get(_to_str(b).replace(".", ""))
    if ca is None or cb is None:
        return False
    return ca == cb


def _to_iso_date(v):
    """Normalize many date formats to yyyy-MM-dd, else None."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)              # 2026-02-24
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", s)        # 24/02/2026 or 24.02.2026
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})$", s)        # 2026.02.24
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def values_match(extracted, gt, threshold: float = None) -> bool:
    thr = threshold if threshold is not None else FUZZY_MATCH_THRESHOLD
    if extracted is None or _to_str(extracted) in ("", "null", "none"):
        return False
    if gt is None or _to_str(gt) == "":
        return False

    # Date-aware: 24/02/2026 == 2026-02-24
    de, dg = _to_iso_date(extracted), _to_iso_date(gt)
    if de is not None and dg is not None:
        return de == dg

    # Unit-aware: KGM == KG, PCE == PC, etc.
    if _unit_equiv(extracted, gt):
        return True

    fn, fg = _to_float(extracted), _to_float(gt)
    if fn is not None and fg is not None:
        if abs(fn - fg) / max(abs(fg), 1e-9) <= 0.01:          # 1% tolerance
            return True
        fn_eu = _to_float_eu_thousands(extracted)
        if fn_eu is not None and abs(fn_eu - fg) / max(abs(fg), 1e-9) <= 0.01:
            return True
        return False

    se, sg = _to_str(extracted), _to_str(gt)
    if se == sg:
        return True
    return SequenceMatcher(None, se, sg).ratio() >= thr


# ═══════════════════════════════════════════════════════════════════════
# 3. LINE-ITEM ALIGNMENT  (replaces the notebook's positional matching)
#    Match by product code, then fuzzy description, disambiguate duplicates.
# ═══════════════════════════════════════════════════════════════════════

def _norm_code(v) -> str:
    return re.sub(r"[\s\-_/]", "", str(v).strip().lower()) if v is not None else ""


def _num_dist(a, b) -> float:
    fa, fb = _to_float(a), _to_float(b)
    if fa is None or fb is None:
        return float("inf")
    return abs(fa - fb) / max(abs(fb), 1e-9)


def align_line_items(ext_items: List[dict], gt_items: List[dict]) -> List[dict]:
    """
    Return a list parallel to gt_items. Each entry is the extracted item matched
    to that GT row (or {} if the engine produced no matching line).
    """
    n = len(gt_items)
    aligned: List[Optional[dict]] = [None] * n
    used: set = set()

    # Shortcut — single line on both sides: match them directly. A one-line
    # invoice has no ambiguity; forcing a product-code/description match would
    # wrongly zero the row when the code differs (e.g. GT null vs engine "Hermes").
    if n == 1 and len(ext_items) == 1:
        return [ext_items[0]], 0

    # Pass 1 — exact product code. For duplicate codes, pick the unused
    # candidate whose amount (then quantity) is closest to the GT row.
    for gi, g in enumerate(gt_items):
        gcode = _norm_code(g.get("productCode"))
        if not gcode:
            continue
        cands = [ei for ei, e in enumerate(ext_items)
                 if ei not in used and _norm_code(e.get("productCode")) == gcode]
        if not cands:
            continue
        if len(cands) == 1:
            best = cands[0]
        else:
            def score(ei):
                e = ext_items[ei]
                return (_num_dist(e.get("amount"), g.get("amount")),
                        _num_dist(e.get("quantity"), g.get("quantity")))
            best = min(cands, key=score)
        aligned[gi] = ext_items[best]
        used.add(best)

    # Pass 2 — multi-signal for still-unmatched GT rows.
    # Score each candidate on a blend of: fuzzy description, gtip match,
    # amount closeness, quantity closeness. Take the best if it clears a bar.
    for gi, g in enumerate(gt_items):
        if aligned[gi] is not None:
            continue
        gdesc = _to_str(g.get("description"))
        ggtip = _norm_code(g.get("gtip"))
        best, best_score = None, 0.0
        for ei, e in enumerate(ext_items):
            if ei in used:
                continue
            score = 0.0
            # description similarity (0..1), weight 0.5
            if gdesc:
                score += 0.5 * SequenceMatcher(None, gdesc, _to_str(e.get("description"))).ratio()
            # gtip exact, weight 0.25
            if ggtip and _norm_code(e.get("gtip")) == ggtip:
                score += 0.25
            # amount within 1%, weight 0.15
            if _num_dist(e.get("amount"), g.get("amount")) <= 0.01:
                score += 0.15
            # quantity within 1%, weight 0.10
            if _num_dist(e.get("quantity"), g.get("quantity")) <= 0.01:
                score += 0.10
            if score > best_score:
                best_score, best = score, ei
        # Accept if the blended evidence is reasonably strong.
        if best is not None and best_score >= 0.55:
            aligned[gi] = ext_items[best]
            used.add(best)

    extra = len(ext_items) - len(used)   # hallucinated / unmatched engine rows
    return [a if a is not None else {} for a in aligned], extra


# ═══════════════════════════════════════════════════════════════════════
# 4. SCORER  (adapted from notebook.calculate_metrics; uses alignment)
# ═══════════════════════════════════════════════════════════════════════

def calculate_metrics(ext_header: dict, ext_items: list, gt: dict) -> dict:
    res = {"header": {}, "items": [], "summary": {}, "extra_lines": 0}

    # Header — score only fields the GT has (non-null) AND not master-data.
    h_total = h_match = 0
    for fld in HEADER_FIELDS:
        if fld in MASTER_DATA_FIELDS:
            continue
        gtv = gt.get(fld)
        if gtv is None or _to_str(gtv) == "":
            continue
        h_total += 1
        extv = ext_header.get(fld)
        ok = values_match(extv, gtv)
        if ok:
            h_match += 1
        res["header"][fld] = {"ground_truth": gtv, "extracted": extv, "matched": ok}

    # Line items — align first, then score per GT row.
    gt_items = gt.get("items", [])
    aligned, extra = align_line_items(ext_items, gt_items)
    res["extra_lines"] = extra
    for gti, ei in zip(gt_items, aligned):
        row = {"fields": {}, "matched": 0, "total": 0}
        for fld in TABLE_FIELDS:
            gtv = gti.get(fld)
            if gtv is None or _to_str(gtv) == "":
                continue
            row["total"] += 1
            extv = ei.get(fld)
            ok = values_match(extv, gtv)
            if ok:
                row["matched"] += 1
            row["fields"][fld] = {"ground_truth": gtv, "extracted": extv, "matched": ok}
        res["items"].append(row)

    it_total = sum(r["total"] for r in res["items"])
    it_match = sum(r["matched"] for r in res["items"])
    tot, matched = h_total + it_total, h_match + it_match
    res["summary"] = {
        "header_total": h_total, "header_matched": h_match,
        "header_accuracy": h_match / h_total if h_total else 0,
        "item_total": it_total, "item_matched": it_match,
        "item_accuracy": it_match / it_total if it_total else 0,
        "total_fields": tot, "total_matched": matched,
        "overall_accuracy": matched / tot if tot else 0,
    }
    return res


# ═══════════════════════════════════════════════════════════════════════
# 5. ADAPTER MAPS  (each engine's native names -> canonical)
# ═══════════════════════════════════════════════════════════════════════

RIERINO_HEADER_MAP = {
    "Total_Weight_Gross": "totalGrossWeight",
    "Total_Weight_Net":   "totalNetWeight",
    "bank":               "bank",
    "carryingMethod":     "carryingMethod",
    "currency":           "currency",
    "deliveryMethod":     "deliveryMethod",
    "dischargeCustoms":   "dischargeCustom",   # spelling differs in Rierino
    "freight":            "freightAmount",
    "insurance":          "insuranceAmount",
    "totalAmount":        "totalAmount",
    "tradeCountryCode":   "tradeCountryCode",
}

RIERINO_LINE_MAP = {
    "amount":              "amount",
    "countryOfOriginCode": "origin",
    "description":         "productDescription",
    "grossWeight":         "grossWeight_invoice",
    "gtip":                "hsCode",
    "invoiceDate":         "invoiceDate",
    "invoiceNo":           "invoiceNo",
    "netWeight":           "netWeight_invoice",
    "productCode":         "productCode",
    "purchaseOrder":       "purchaseOrder",
    "quantity":            "quantity",
    "quantityUnitCode":    "quantityUnitCode",
}


def _unwrap(cell):
    """Rierino values are {'value':..,'confidence':..,'source':..}."""
    if isinstance(cell, dict) and "value" in cell:
        return cell.get("value")
    return cell


# ═══════════════════════════════════════════════════════════════════════
# 6. RIERINO ADAPTER  (login -> upload -> ProcessPDF -> map to canonical)
# ═══════════════════════════════════════════════════════════════════════

class RierinoClient:
    def __init__(self, base_url, username, password, timeout=600):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout

    def _login(self, s):
        r = s.post(f"{self.base_url}/api/auth/login/rpc",
                   auth=HTTPBasicAuth(self.username, self.password), timeout=self.timeout)
        r.raise_for_status()
        tok = r.json().get("gateway_token")
        if not tok:
            raise RuntimeError("Rierino: no gateway_token")
        return tok

    def process(self, pdf_bytes: bytes, filename: str = None) -> dict:
        from urllib.parse import quote
        filename = filename or f"bench_{uuid.uuid4().hex[:8]}.pdf"
        remote = f"mail/manual/{filename}"
        with requests.Session() as s:
            tok = self._login(s)
            enc = quote(remote, safe="/")
            up = s.put(f"{self.base_url}/api/file/sudo/fs_evrim/{enc}",
                       headers={"Authorization": f"gateway_token {tok}"},
                       files={"file": (filename, io.BytesIO(pdf_bytes), "application/pdf")},
                       timeout=self.timeout)
            up.raise_for_status()
            pr = s.post(f"{self.base_url}/api/request/main_rpc/ProcessPDF",
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"gateway_token {tok}"},
                        json={"file": f"/file/{remote}"}, timeout=self.timeout)
            pr.raise_for_status()
            return pr.json()


def run_rierino(pdf_bytes: bytes, cfg: dict) -> dict:
    """Returns {header, items, latency, cost, ocr_text, raw}."""
    t0 = time.time()
    client = RierinoClient(cfg["base_url"], cfg["username"], cfg["password"])
    resp = client.process(pdf_bytes)
    latency = time.time() - t0

    data = resp.get("declaration", {}).get("data", {})
    ocr_text = (resp.get("content") or {}).get("md", "")

    header = {}
    for canon, native in RIERINO_HEADER_MAP.items():
        header[canon] = _unwrap(data.get(native))
    # gaps -> None
    for f in HEADER_FIELDS:
        header.setdefault(f, None)

    header_currency = header.get("currency")
    items = []
    for li in data.get("lineItem", []):
        item = {}
        for canon, native in RIERINO_LINE_MAP.items():
            item[canon] = _unwrap(li.get(native))
        item["currency"] = header_currency        # Rierino holds currency at header
        for f in TABLE_FIELDS:
            item.setdefault(f, None)
        items.append(item)

    return {"header": header, "items": items, "latency": latency,
            "cost": 0.0, "ocr_text": ocr_text, "raw": resp}   # Rierino = own infra, no per-call price


# ═══════════════════════════════════════════════════════════════════════
# 7. CLAUDE ADAPTER  (Anthropic API, reads the PDF directly, returns canonical)
# ═══════════════════════════════════════════════════════════════════════

CLAUDE_SYSTEM_PROMPT = """You are an expert customs declaration extraction engine.
You will be given a commercial invoice PDF. Extract fields into a SINGLE valid JSON
object using EXACTLY the field names below. Return JSON only — no prose, no code fences.

HEADER (flat, top level). Use null when not present on the invoice:
  Total_Weight_Gross (number, KG), Total_Weight_Net (number, KG),
  bank, carryingMethod (SEA/AIR/ROAD/RAIL/POST/MULTIMODAL),
  consignmentNo, currency (ISO 4217), deliveryMethod (Incoterm code: EXW FCA CPT CIP DAP DPU DDP FAS FOB CFR CIF),
  dischargeCustoms, discount (number), documentType, freight (number), freightCurrency (ISO 4217),
  insurance (number), insuranceCurrency (ISO 4217), paymentMethod, termOfPayment,
  totalAmount (number), tradeCountryCode (ISO alpha-2, country goods ship FROM).
  Do NOT output buyerVKN, supplierVKN, buyerCode, supplierCode — these are master data, not on the invoice.

LINE ITEMS as "items": [ ... ], one object per invoice line, EACH with:
  amount (number), countryOfOriginCode (ISO alpha-2), currency (ISO 4217),
  description, discountItem (number), grossWeight (number KG),
  gtip (HS/commodity code, digits only, keep ALL digits exactly),
  invoiceDate (yyyy-MM-dd), invoiceNo, netWeight (number KG),
  productCode, purchaseOrder, quantity (number),
  quantityUnitCode (UN/CEFACT: PCE KGM MTR LTR MTQ), unitPrice (number), weightUnitCode.

RULES:
- Extract EVERY line item as a separate object; never aggregate; 10-200+ possible.
- invoiceNo and invoiceDate come from the header; assign to every line item.
- Convert numbers to plain JSON numbers (dot decimal, no thousands separators).
- Incoterm: extract only the code, ignore city ("CPT Istanbul" -> "CPT").
- Country names -> ISO alpha-2 (China->CN, Germany->DE, Czech Republic->CZ, Turkey->TR).
- Dates -> yyyy-MM-dd.
- Never fabricate; use null when absent.
Return only the JSON object.
"""


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Output was likely truncated at the token limit. Recover what we can:
        # close the "items" array at the last complete object and close the root.
        salvaged = _salvage_truncated_json(text)
        if salvaged is not None:
            return salvaged
        raise


def _salvage_truncated_json(text: str) -> Optional[dict]:
    """
    Best-effort recovery of a JSON object truncated mid-array. Keeps the header
    fields and every fully-closed item; drops the half-written trailing item.
    """
    try:
        # find the items array
        key = '"items"'
        ki = text.find(key)
        if ki == -1:
            # no items array — trim to the last complete "key": value pair
            # cut at the last comma that is not inside a string, then close.
            depth = 0
            in_str = False
            esc = False
            last_comma = -1
            for i, c in enumerate(text):
                if in_str:
                    if esc: esc = False
                    elif c == "\\": esc = True
                    elif c == '"': in_str = False
                    continue
                if c == '"': in_str = True
                elif c == "{": depth += 1
                elif c == "}": depth -= 1
                elif c == "," and depth == 1:
                    last_comma = i
            if last_comma == -1:
                return None
            candidate = text[:last_comma] + "}"
            return json.loads(candidate)
        # walk to the '[' after items
        lb = text.find("[", ki)
        if lb == -1:
            return None
        # scan objects inside the array, tracking the end of the last complete one
        depth = 0
        in_str = False
        esc = False
        last_complete = None
        for i in range(lb + 1, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    last_complete = i
        if last_complete is None:
            # no complete item; make items empty
            head = text[:lb + 1] + "]}"
            return json.loads(head)
        rebuilt = text[:last_complete + 1] + "]}"
        return json.loads(rebuilt)
    except Exception:
        return None


def run_claude(pdf_bytes: bytes, cfg: dict) -> dict:
    import anthropic
    t0 = time.time()
    client = anthropic.Anthropic(api_key=cfg["api_key"])
    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    content = [
        {"type": "document",
         "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
        {"type": "text", "text": "Extract this invoice into the canonical JSON schema."},
    ]

    def _call(max_tok):
        return client.messages.create(
            model=cfg.get("model", "claude-sonnet-5"),
            max_tokens=max_tok,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

    # Try a high limit for long invoices; if the model rejects it, step down.
    want = cfg.get("max_tokens", 16000)
    try:
        msg = _call(want)
    except Exception:
        msg = _call(8192)   # safe floor supported by all current models
    latency = time.time() - t0
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    parsed = _parse_json(raw)

    header = {f: parsed.get(f) for f in HEADER_FIELDS}
    items = []
    for li in parsed.get("items", []):
        items.append({f: li.get(f) for f in TABLE_FIELDS})

    in_tok = msg.usage.input_tokens
    out_tok = msg.usage.output_tokens
    price_in = cfg.get("price_in_per_mtok", 3.0)     # USD / 1M input tokens — VERIFY at docs.claude.com
    price_out = cfg.get("price_out_per_mtok", 15.0)  # USD / 1M output tokens — VERIFY
    cost = in_tok / 1e6 * price_in + out_tok / 1e6 * price_out

    return {"header": header, "items": items, "latency": latency,
            "cost": cost, "tokens": (in_tok, out_tok), "raw": parsed}


# ═══════════════════════════════════════════════════════════════════════
# 8. NANONETS ADAPTER  (v2 OCR; confirm model_id + version before use)
# ═══════════════════════════════════════════════════════════════════════

NANONETS_LABEL_MAP = {   # nanonets label -> canonical. Adjust to your model's labels.
    # header
    "Total_Weight_Gross": "Total_Weight_Gross", "Total_Weight_Net": "Total_Weight_Net",
    "bank": "bank", "carryingMethod": "carryingMethod", "currency": "currency",
    "deliveryMethod": "deliveryMethod", "dischargeCustoms": "dischargeCustoms",
    "freight": "freight", "insurance": "insurance", "paymentMethod": "paymentMethod",
    "termOfPayment": "termOfPayment", "totalAmount": "totalAmount",
    "tradeCountryCode": "tradeCountryCode",
    # line
    "amount": "amount", "countryOfOriginCode": "countryOfOriginCode",
    "description": "description", "grossWeight": "grossWeight", "gtip": "gtip",
    "invoiceDate": "invoiceDate", "invoiceNo": "invoiceNo", "netWeight": "netWeight",
    "productCode": "productCode", "purchaseOrder": "purchaseOrder", "quantity": "quantity",
    "quantityUnitCode": "quantityUnitCode", "unitPrice": "unitPrice",
    "weightUnitCode": "weightUnitCode",
}


def parse_nanonets_table(cells):
    """Cells -> row dicts. A new row starts when a label repeats."""
    rows, cur, seen = [], {}, set()
    for c in cells:
        lbl, text = c.get("label", "").strip(), c.get("ocr_text", c.get("text", "")).strip()
        if not lbl:
            continue
        if lbl in seen:
            if cur:
                rows.append(cur)
            cur, seen = {}, set()
        cur[lbl] = text
        seen.add(lbl)
    if cur:
        rows.append(cur)
    return rows


def run_nanonets(pdf_bytes: bytes, cfg: dict) -> dict:
    t0 = time.time()
    model_id = cfg["model_id"]
    url = f"https://app.nanonets.com/api/v2/OCR/Model/{model_id}/LabelFile/"
    r = requests.post(url, auth=HTTPBasicAuth(cfg["api_key"], ""),
                      files={"file": ("invoice.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                      timeout=cfg.get("timeout", 300))
    r.raise_for_status()
    data = r.json()
    latency = time.time() - t0

    # Collect predictions across pages.
    preds = []
    for page in data.get("result", []):
        preds.extend(page.get("prediction", []))

    header, table_cells = {}, []
    for p in preds:
        label = p.get("label", "")
        if p.get("type") == "table" or "cells" in p:
            table_cells.extend(p.get("cells", []))
        else:
            canon = NANONETS_LABEL_MAP.get(label)
            if canon and canon in HEADER_FIELDS:
                header[canon] = p.get("ocr_text", p.get("text"))

    rows = parse_nanonets_table(table_cells)
    items = []
    for row in rows:
        item = {}
        for lbl, val in row.items():
            canon = NANONETS_LABEL_MAP.get(lbl)
            if canon and canon in TABLE_FIELDS:
                item[canon] = val
        if item:
            items.append(item)

    for f in HEADER_FIELDS:
        header.setdefault(f, None)

    pages = len(data.get("result", [])) or 1
    cost = pages * cfg.get("price_per_page", 0.0)   # set per your Nanonets plan

    return {"header": header, "items": items, "latency": latency,
            "cost": cost, "raw": data}


# ═══════════════════════════════════════════════════════════════════════
# 9. GEMINI ADAPTER  (stub until AI Studio key is provided)
# ═══════════════════════════════════════════════════════════════════════

def run_gemini(pdf_bytes: bytes, cfg: dict) -> dict:
    """Gemini reads the PDF directly and returns the canonical schema."""
    import google.generativeai as genai
    t0 = time.time()
    genai.configure(api_key=cfg["api_key"])
    model = genai.GenerativeModel(
        model_name=cfg.get("model", "gemini-2.5-flash"),
        system_instruction=CLAUDE_SYSTEM_PROMPT,   # same canonical-schema instructions
    )
    resp = model.generate_content(
        [
            {"mime_type": "application/pdf", "data": pdf_bytes},
            "Extract this invoice into the canonical JSON schema.",
        ],
        generation_config={"temperature": 0, "max_output_tokens": 32000},
    )
    latency = time.time() - t0
    parsed = _parse_json(resp.text)

    header = {f: parsed.get(f) for f in HEADER_FIELDS}
    items = [{f: li.get(f) for f in TABLE_FIELDS} for li in parsed.get("items", [])]

    um = getattr(resp, "usage_metadata", None)
    in_tok = getattr(um, "prompt_token_count", 0) if um else 0
    out_tok = getattr(um, "candidates_token_count", 0) if um else 0
    price_in = cfg.get("price_in_per_mtok", 0.30)    # VERIFY at ai.google.dev/pricing
    price_out = cfg.get("price_out_per_mtok", 2.50)  # VERIFY
    cost = in_tok / 1e6 * price_in + out_tok / 1e6 * price_out

    return {"header": header, "items": items, "latency": latency,
            "cost": cost, "tokens": (in_tok, out_tok), "raw": parsed}


# ═══════════════════════════════════════════════════════════════════════
# 10. DISPATCH
# ═══════════════════════════════════════════════════════════════════════

ENGINES = {
    "Rierino":  run_rierino,
    "Claude":   run_claude,
    "Nanonets": run_nanonets,
    "Gemini":   run_gemini,
}


def run_engine(name: str, pdf_bytes: bytes, cfg: dict) -> dict:
    return ENGINES[name](pdf_bytes, cfg)
