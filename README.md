# AI Engineer Assessment Platform

An integrated prototype for the four assignment questions. It uses the **Groq API** for grounded replies, multilingual speech-to-text, English text-to-speech, and call-signal analysis. No Vapi account or telephony provider is required.

## What is implemented

- **Q1 voice agent** (`app/agent.py`, docs in `docs/VOICE_AGENT.md`): a browser voice call for health-insurance lead qualification. It keeps state across turns, collects lead details in the playbook order, answers questions from the Q2 knowledge base with citations, handles objections, reads back conflicting answers, applies qualification rules in code, escalates to a human, and writes leads, callbacks and escalations to a mock CRM with an optional webhook. Code guards block invented numbers, guarantees and claims about unsupported products, and enforce the required disclosures.
- **Q2 knowledge base** (`app/ingest.py`, `app/kb.py`, docs in `docs/KB_DESIGN.md` and `docs/RETRIEVAL_EVAL.md`): ingestion of messy web, PDF, CSV, form and Markdown sources (cleaning, PII redaction, deduplication, versioning, quarantining of source errors), producing 30 cited records. Retrieval is BM25 with grounding gates and is evaluated on tuning and held-out query sets.
- Groq Whisper speech-to-text and English TTS for the browser call; OpenAI for the Philippines and Indonesia markets.
- Real-time signal and nudge generation for Q4, with confidence thresholds, cooldowns and duplicate suppression.
- Offline tests: `pytest` never calls a provider.

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
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System and ingestion diagrams, design decisions |
| [KB_DESIGN.md](docs/KB_DESIGN.md) | Q2: schema, cleaning, chunking, versioning, citations |
| [RETRIEVAL_EVAL.md](docs/RETRIEVAL_EVAL.md) | Q2: 42 queries with sources, explanations and verdicts |
| [VOICE_AGENT.md](docs/VOICE_AGENT.md) | Q1: flow, qualification rules, safety guards, API |
| [Q1_SCENARIOS.md](docs/Q1_SCENARIOS.md) | Q1: every supported scenario, with example phrases |
| [Q1_TEST_CALLS.md](docs/Q1_TEST_CALLS.md) | Q1: 12 scripted call transcripts and results |
| [GROQ_SETUP.md](docs/GROQ_SETUP.md) | API key and model configuration |
| [Evidence/](Evidence/README.md) | Recorded calls: audio, transcripts, coverage map |
