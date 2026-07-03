# Evrim — Engine Benchmark

Compares invoice-extraction engines (**Rierino, Claude, Nanonets, Gemini**) against a
manually keyed ground truth. Every engine's output is converted into one **canonical
schema** (22 header + 16 line fields), line items are aligned by product code, and each
engine is scored field-by-field.

## What it does
- Upload a **PDF** + its **ground-truth JSON** (canonical schema).
- Pick engines; each runs on the PDF and returns canonical fields.
- Scores header accuracy, line accuracy, overall — plus latency and cost.
- Side-by-side comparison + per-engine field detail (match/miss).

## Setup

```bash
cd evrim_benchmark
pip install -r requirements.txt

# add your keys/endpoints:
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
#   then edit .streamlit/secrets.toml

streamlit run app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

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
