# Darwix AI — Voice Agents for Insurance & Consumer Finance

One FastAPI application covering all four parts of the AI Engineer Assessment: a knowledge-grounded
voice agent, the knowledge base behind it, two native-language bots, and a live layer that nudges a
human agent while a call is in progress.

The market is chosen in the browser and everything below it follows — knowledge base, call flow,
language, speech-to-text and voice. All company names, policies, prices and customers are synthetic.

Speech-to-text and the language model run on **Groq**. The Philippine and Indonesian voices run
**locally** (Meta MMS-TTS), so they need no API key and no quota. No telephony provider is required.

## What is implemented

| | Scope | Documentation |
|---|---|---|
| **Q1** | Browser voice agent for US health-insurance lead qualification: stateful call, cited answers, objection handling, conflict read-back, qualification rules in code, human escalation, mock CRM | [VOICE_AGENT.md](docs/VOICE_AGENT.md) |
| **Q2** | Knowledge base built from messy web, PDF, CSV and form sources: cleaning, PII redaction, deduplication, versioning, source-error quarantine; BM25 retrieval with grounding gates | [KB_DESIGN.md](docs/KB_DESIGN.md) · [RETRIEVAL_EVAL.md](docs/RETRIEVAL_EVAL.md) |
| **Q3** | Philippines (Taglish, life-insurance renewal) and Indonesia (Bahasa, installment reminder) bots with their own sectors, flows, compliance rules and native voices | [Q3_MARKETS.md](docs/Q3_MARKETS.md) |
| **Q4** | Live nudges from a call in progress: streaming ASR, stateful signals, suppression controls, measured P50/P95 latency, false-positive analysis | [Q4_REALTIME.md](docs/Q4_REALTIME.md) |

**Measured results.** Retrieval 82% top-1 on a held-out query set with 7/7 out-of-scope questions
rejected. Indonesian ASR mean WER 0.056 with 13/13 finance terms retained. Live nudges at P50 298 ms
end to end on real call audio, with precision 1.00 and a 0.00 false-positive rate on the labelled
set. **126 offline tests** that need no credentials and no network.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # add your GROQ_API_KEY
uvicorn app.main:app --reload   # http://127.0.0.1:8000/
```

Pick a market in the dropdown, press the call button and speak — or type, using the text box.
`http://127.0.0.1:8000/docs` lists every endpoint.

Optional, for the native Philippine and Indonesian voices (~260 MB, downloaded once):

```bash
pip install -e '.[local-tts]'
```

Without it the browser's own voice is used and the UI says so on screen.

The knowledge base is committed, so it only needs rebuilding if you change `data/raw/`:

```bash
python -m app.ingest            # all three markets
```

## Reproducing the measurements

Every number in the documentation comes from one of these.

| Command | Produces |
|---|---|
| `pytest` | 126 offline tests across all four questions |
| `python scripts/eval_retrieval.py` | Q2 retrieval table → `docs/RETRIEVAL_EVAL.md` |
| `python scripts/run_test_calls.py` | Q1: 12 scripted calls through the live model |
| `python scripts/asr_check.py` | Q3: per-market ASR word error rate and term retention |
| `python scripts/simulate_call.py ph1` | Q3: a whole call as audio, end to end |
| `python scripts/replay_call.py --scenario compliance` | Q4: a call replayed at real-time speed with live nudges |
| `python scripts/eval_nudges.py` | Q4: precision, recall and false-positive rate |

Q4 is deliberately not post-hoc: `replay_call.py` releases each chunk only when its moment arrives,
so a 92-second call takes 92 seconds.

## Layout

```
app/        agent.py          Q1 lead-qualification agent, safety guards
            reminder_agent.py Q3 Philippines / Indonesia reminder agent
            flows.py          Q3 market scripts, intents, compliance rewrites
            ingest.py         Q2 knowledge-base build
            market_vocab.py   per-market cleaning vocabularies
            kb.py             retrieval: BM25, expansions, grounding gates
            signals.py        Q4 signal extraction
            nudges.py         Q4 nudge generation and suppression
            stream.py         Q4 real-time pipeline and latency
            llm.py            Groq adapters
            local_tts.py      native Tagalog / Indonesian voices
            crm.py            mock CRM
            main.py           API and browser UI
data/       raw/              source documents per market
            kb/               built records, one set per market
            eval/             labelled query, ASR and nudge sets
            rules/            qualification rules
            crm/              leads, callbacks, escalations, promises
docs/       design, evaluation and limitations
scripts/    evaluation and demo harnesses
tests/      126 offline tests
Evidence/   recorded calls with transcripts
```

## Documentation

| Document | Contents |
|---|---|
| [ASSESSMENT_REPORT.md](docs/ASSESSMENT_REPORT.md) | **Start here** — what was built, with results |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System and pipeline diagrams, design decisions |
| [architecture.html](docs/architecture.html) | The same diagram rendered, for screen-sharing |
| [VOICE_AGENT.md](docs/VOICE_AGENT.md) | Q1: call flow, qualification rules, safety guards, API |
| [KB_DESIGN.md](docs/KB_DESIGN.md) | Q2: schema, cleaning, chunking, versioning, citations |
| [RETRIEVAL_EVAL.md](docs/RETRIEVAL_EVAL.md) | Q2: 42 queries with sources, explanations and verdicts |
| [Q3_MARKETS.md](docs/Q3_MARKETS.md) | Q3: localization, ASR report, cross-lingual retrieval, native TTS |
| [Q4_REALTIME.md](docs/Q4_REALTIME.md) | Q4: signal design, nudge control, latency, false positives |
| [GROQ_SETUP.md](docs/GROQ_SETUP.md) | API key and model configuration |

## Evidence

| | |
|---|---|
| `Demo_Video.mp4` | Walkthrough: architecture, live calls, limitations |
| [Evidence/](Evidence/README.md) | Q1: four recorded calls with transcripts and coverage map |
| [Evidence/q3/](Evidence/q3/README.md) | Q3: four end-to-end calls, two per market |

## Key design decision

**The language model writes the words; the code owns the decisions.** Eligibility, escalation,
compliance wording and question order are deterministic and unit-tested, so neither the caller nor
the model can talk the system out of a rule. Every reply is checked before it is spoken: no invented
numbers, no promises of approval, the underwriting disclosure before any price, and no drifting out
of the caller's language. If the provider fails or rate-limits, the call continues on rules.

## Known limitations

Documented in full in [ASSESSMENT_REPORT.md](docs/ASSESSMENT_REPORT.md) and the per-question docs.
The most important: **no native speaker has reviewed the Taglish or Bahasa content**, which in a
collections context is a compliance risk rather than a matter of style. Retrieval is lexical rather
than embedding-based. Q4 signals are English-only. Call state is in memory, so horizontal scaling
needs a shared store.
