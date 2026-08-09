# Evrim — Konsensus Fatura Cikarim

Ayni fatura PDF'i 4 motora birden verilir (**Rierino, Claude, Nanonets, Gemini**), her
motorun ciktisi ortak bir **kanonik semaya** (22 baslik + 16 satir alani) cevrilir, ve
her alanda (hem baslik hem urun satirlari) motorlarin **birbirleriyle** ne kadar uzlastigi
gosterilir. **Ground truth (elle hazirlanmis dogru cevap) YOK** — referans, motorlarin
kendisidir. Detay ve tasarim kararlari icin: [`HANDOVER.md`](HANDOVER.md).

## Ana arac: `consensus_app.py` (canli, ground-truth'suz)
- Bir **PDF** yukle, motorlari sec, calistir.
- Her baslik alaninda VE her urun satirinin her alaninda: motorlarin degeri yan yana,
  uzlasma yuzdesi, cogunlugun onerdigi deger, "kontrol edilmeli" isareti.
- Satir hizalama ground truth olmadan yapilir (motorlar farkli sayida/sirada kalem
  uretir) — `consensus_core.py::consensus_lines`.
- Tek calistirmaya ozel motor ozeti: hangi motor kac alani cevapladi, cogunlukla
  hemfikir miydi (bu bir "motor profili" degil, sadece o faturaya ozel bir gozlem).
- Bir motorun ciktisi token limitinde kesilirse acik uyari gosterir (`truncated`).

```bash
cd evrim_benchmark
pip install -r requirements.txt

mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
#   then edit .streamlit/secrets.toml

streamlit run consensus_app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

## Ikincil arac: `app.py` (eski, ground-truth'lu benchmark)
Elle hazirlanmis dogru-cevap (ground truth) JSON'u olan faturalarla motorlarin
**dogrulugunu** olcmek icin. Konsensus aracindan farkli amac: burada "dogru cevap"
bilinir, motorlar ona karsi puanlanir. Ic QA / motor secimi icin faydali, canli
kullanicilara gosterilen arac bu DEGIL.
- Upload a **PDF** + its **ground-truth JSON** (canonical schema).
- Pick engines; each runs on the PDF and returns canonical fields.
- Scores header accuracy, line accuracy, overall — plus latency and cost.
- Side-by-side comparison + per-engine field detail (match/miss).

```bash
streamlit run app.py
```

## Engine status
| Engine | Status | Needs |
|---|---|---|
| Rierino | ready | endpoint reachable + credentials (in secrets) |
| Claude | ready | Anthropic API key |
| Nanonets | ready when configured | rotated API key + model ID (+ confirm v2 OCR) |
| Gemini | stubbed | AI Studio key + wire `run_gemini` in `benchmark_core.py` |

An engine's checkbox is disabled until its secrets are present.

## Ground-truth format
Flat header fields + `items: [...]`, using canonical names. Only include fields that are
on the invoice — the scorer grades a field only when the ground truth has a value for it.
Master-data fields (`buyerVKN`, `supplierVKN`, `buyerCode`, `supplierCode`) are **excluded
from scoring** (not on the invoice). See the in-app "Ground truth formatı" example.

## Notes & known dependencies
- **Rierino reachability:** the app calls `http://16.171.20.18:8080`. Your office IP is
  whitelisted; a cloud-hosted app has a *different* IP. To share a hosted link, either get
  the host IP whitelisted or run on Evrim's network. Locally it works from a whitelisted IP.
- **Nanonets adapter** targets the v2 OCR endpoint and a generic label map; adjust
  `NANONETS_LABEL_MAP` in `benchmark_core.py` to your model's actual labels once confirmed.
- **Pricing** is configurable in secrets (verify Anthropic pricing at docs.claude.com and
  set your Nanonets per-page rate). Rierino cost is 0 (own infrastructure).
- **Line-item alignment** is by product code (then fuzzy description), not position —
  handles reordered rows and duplicate product codes.
- **Scoring** is deterministic (numbers with 1% tolerance + EU/US formats; codes/dates exact;
  strings fuzzy ≥ 0.85). LLM-as-judge for semantic fields is a later upgrade.

## Deploy (share a link with Zafer / product team)
Streamlit Community Cloud: push this folder to a private repo, add the same keys under the
app's **Secrets** settings (same TOML), deploy. Confirm the deployed app can reach Rierino
(see reachability note) — otherwise run Rierino-less in the cloud and keep Rierino for local runs.
