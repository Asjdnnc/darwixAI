# AI Engineer Assessment Platform

An integrated prototype for the four assignment questions. It uses the **Groq API** for grounded replies, multilingual speech-to-text, English text-to-speech, and call-signal analysis. No Vapi account or telephony provider is required.

## What is implemented

| | Scope | Evidence |
|---|---|---|
| **Q1** | Browser voice agent for US health-insurance lead qualification: stateful call, cited answers, objection handling, conflict read-back, qualification rules in code, human escalation, mock CRM | [VOICE_AGENT.md](docs/VOICE_AGENT.md) · [Q1_TEST_CALLS.md](docs/Q1_TEST_CALLS.md) · `Evidence/` |
| **Q2** | Knowledge base from messy web/PDF/CSV/form sources: cleaning, PII redaction, dedup, versioning, quarantine; BM25 retrieval with grounding gates | [KB_DESIGN.md](docs/KB_DESIGN.md) · [RETRIEVAL_EVAL.md](docs/RETRIEVAL_EVAL.md) |
| **Q3** | Philippines (Taglish, life insurance renewal) and Indonesia (Bahasa, installment reminder) bots with their own sectors, flows, compliance rules and native voices | [Q3_MARKETS.md](docs/Q3_MARKETS.md) · `Evidence/q3/` |
| **Q4** | Live nudges from a call in progress: streaming ASR, stateful signals, suppression controls, measured P50/P95 latency, false-positive analysis | [Q4_REALTIME.md](docs/Q4_REALTIME.md) |

Headline numbers: retrieval 82% top-1 on held-out queries with 7/7 out-of-scope rejected; Indonesian
ASR mean WER 0.056; Q4 nudges at P50 298 ms end to end on real audio with precision 1.00 and a 0.00
false-positive rate on the labelled set. **126 offline tests**, no credentials or network required.

## Quick start

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m app.ingest                 # rebuild data/kb from data/raw (already committed)
uvicorn app.main:app --reload        # open http://127.0.0.1:8000/ for the voice call
```

Open `http://127.0.0.1:8000/docs`. Run `pytest` before every demo.

## Configure Groq

Set `GROQ_API_KEY` and optionally select the text, STT, and TTS models in `.env`. Groq is called with a narrow, citation-required prompt; provider errors degrade safely to an escalation response. Use `/voice/transcribe` for browser microphone audio and `/voice/speak` for English audio replies.

Groq currently exposes English and Arabic TTS models, so Philippines and Indonesia flows return localized text while the report records this native-TTS limitation.

## Important demo rule

For Question 4, replay audio or transcript chunks at real-time speed through `/demo/replay`. The API records per-chunk receipt, analysis, and nudge timestamps and reports P50/P95 latency. A completed file analyzed after upload is not a qualifying demo.

Evidence scripts: `python scripts/eval_retrieval.py` (Q2 retrieval table) and `python scripts/run_test_calls.py` (Q1 scripted calls through live Groq, written to `docs/Q1_TEST_CALLS.md`).

## Documentation

| Document | Contents |
|---|---|
| [ASSESSMENT_REPORT.md](docs/ASSESSMENT_REPORT.md) | **Start here** — what was built, with results |
| [Q3_MARKETS.md](docs/Q3_MARKETS.md) | Q3: localization, ASR report, cross-lingual retrieval, native TTS |
| [Q4_REALTIME.md](docs/Q4_REALTIME.md) | Q4: signal design, nudge control, latency, false positives |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System and ingestion diagrams, design decisions |
| [architecture-simple.html](docs/architecture-simple.html) | One-screen diagram for the video (light) |
| [architecture.html](docs/architecture.html) | Detailed diagram with endpoints and latencies (dark) |
| [KB_DESIGN.md](docs/KB_DESIGN.md) | Q2: schema, cleaning, chunking, versioning, citations |
| [RETRIEVAL_EVAL.md](docs/RETRIEVAL_EVAL.md) | Q2: 42 queries with sources, explanations and verdicts |
| [VOICE_AGENT.md](docs/VOICE_AGENT.md) | Q1: flow, qualification rules, safety guards, API |
| [Q1_SCENARIOS.md](docs/Q1_SCENARIOS.md) | Q1: every supported scenario, with example phrases |
| [Q1_TEST_CALLS.md](docs/Q1_TEST_CALLS.md) | Q1: 12 scripted call transcripts and results |
| [GROQ_SETUP.md](docs/GROQ_SETUP.md) | API key and model configuration |
| [Evidence/](Evidence/README.md) | Recorded calls: audio, transcripts, coverage map |
