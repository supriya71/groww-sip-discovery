# Grow ChatBot — SIP retention discovery + RAG prototype

This repo has two tracks:

1. **Discovery engine (PM brief)** — ingest public conversations about first-time SIP investing, label themes, and rank user problems that affect **12-month active SIP** retention. See `Docs/Project-Brief-SIP-12M-Retention-Discovery-Engine.md`.
2. **RAG FAQ prototype** — facts-only chatbot over five HDFC fund pages on Groww (`code/`, `Docs/PRD-Groww-HDFC-RAG-Chatbot.md`).

## Setup

```powershell
cd "Grow ChatBot"
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

Configuration for collectors lives in **`config.yaml`** at the repo root.

## Collectors (discovery corpus)

Run from repo root with the venv activated:

```powershell
python -m collect.run_all play_store
```

**Play Store** fetches public reviews for apps listed under `play_store.apps`, writes:

| Output | Purpose |
| --- | --- |
| `data/raw/play_store/all_reviews.jsonl` | Every review pulled |
| `data/raw/play_store/kept_reviews.jsonl` | Reviews matching SIP/MF keyword filter |
| `data/raw/play_store/manifest.json` | Counts and run metadata |

Keyword lists and app IDs are edited in `config.yaml` (`play_store.keyword_filter`, `play_store.apps`).

## Discovery dashboard (Streamlit)

Build the read-only export bundle, then run the PM dashboard (reads **`data/export/`** only):

```powershell
python -m pipeline.export
streamlit run app/streamlit_app.py
```

Page screenshots (local run): `docs/app_screenshots/`.

## RAG prototype (optional)

```powershell
streamlit run code/ui/app.py
```

Pipeline stages: `code/loading`, `code/chunking`, `code/embedding`, `code/vectordb`, `code/retrieval`.

## Guardrails (discovery phase)

- No fund or stock recommendations in product outputs.
- No fear, FOMO, or urgency-based nudges.
- Public sources only for the discovery corpus; redact PII if present in text.
