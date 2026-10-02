# Architecture

All four questions share one FastAPI app, one browser UI and one knowledge-base pipeline. All
content is synthetic.

| | Q1 | Q2 | Q3 | Q4 |
|---|---|---|---|---|
| What | Voice agent | Knowledge base | Native-language bots | Live nudges |
| Market | 🇺🇸 US health | — | 🇵🇭 PH life · 🇮🇩 ID multifinance | any |
| Entry point | `/` → `/agent/turn` | `python -m app.ingest` | `/` market dropdown | `/calls/{id}/audio`, `/ws/nudges/{id}` |

## System

```text
┌─ BROWSER (app/static/index.html) ─────────────────────────────────────────────┐
│  market dropdown · mic + silence detection · chat with citations              │
│  lead / commitment panel · live nudge panel (priority, confidence, latency)   │
└──────┬──────────────────────────────────────────────────▲─────────────────────┘
       │ audio, text                                      │ reply · citations · nudges
       ▼                                                  │
┌─ FastAPI (app/main.py) ──────────────────────────────────┴────────────────────┐
│  /voice/transcribe   Groq Whisper (en · tl · id, market-specific prompts)     │
│  /calls/start        open a call in the selected market                       │
│  /agent/turn   ──►   en: LeadQualificationAgent      (Q1, app/agent.py)       │
│                      ph|id: ReminderAgent            (Q3, app/reminder_agent) │
│  /voice/speak        en: Groq Orpheus · ph|id: local MMS-TTS · browser        │
│  /calls/transcript   ──► RealtimePipeline            (Q4, app/stream.py)      │
│  /calls/{id}/audio   ──► ASR ─► signals ─► nudges ─► WebSocket                │
│  /retrieve /markets /crm/{kind} /calls/{id}/report                            │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Q1 + Q3 — one turn of a call

```text
caller text
  1. deterministic intent checks ....... human request · recording refused · already paid
  2. RETRIEVE (Q2) ..................... market knowledge base, score + coverage gates
  3. BRAIN ............................. Groq JSON: reply, citations, intent, fields
  │                                      ├─ prose recovery when JSON validation fails
  │                                      └─ rule-based brain if the provider fails
  4. MERGE ............................. caller's own words win; changes are read back
  5. RULES ............................. Q1 eligibility · Q3 promise/hardship/dispute
  6. BUSINESS ACTION ................... CRM lead · callback · escalation · promise
  7. GUARDS ............................ unsupported question · invented numbers ·
  ▼                                      prohibited promises · disclosure before price ·
reply + citations                        language drift · unfilled placeholders
```

## Q2 — knowledge base build (offline)

```text
data/raw/{en,ph,id}/**   web · PDF text · forms · CSV · markdown · scanned PDF
   │  manifest.json: origin, version, authority, supersedes
   ▼
EXTRACT ─► CLEAN ─► PROTECT ─► NORMALIZE ─► VALIDATE ─► DEDUPE ─► PUBLISH
            nav,      PII       headings,    quarantine  near-dup   record ids
            footers,  redaction dates (ISO), impossible  by source
            boilerplate         terminology  values      authority
   │  per-market vocabularies (app/market_vocab.py)
   ▼
data/kb/{,ph/,id/}records.jsonl   en 30 · ph 30 · id 32 cited records
   └─► KnowledgeBase: BM25 + query expansion + coverage gates, per market
```

## Q4 — one chunk of a live call

```text
audio chunk (released only when its moment arrives in wall-clock time)
  │ t_received
  ├─ ASR ................. Groq Whisper, prompt-echo chunks discarded   ~298 ms P50
  ├─ SIGNALS ............. stateful rules; optional LLM pass (off)       ~0.1 ms
  ├─ NUDGES .............. threshold · cooldown · dedup · grouping ·     ~0.0 ms
  │                        priority · expiry · repetition limit
  └─ DELIVER ............. WebSocket · polling · report                  ~0.1 ms
```

## Key design decisions

| Decision | Why |
|---|---|
| **Code owns outcomes, the model owns language** | Eligibility, escalation, compliance wording and question order are deterministic and unit-tested, so they cannot be talked around by the model or the caller. |
| **Grounding gates before the model sees anything** | The model can only cite what retrieval passed. No evidence means the agent says so, rather than improvising. |
| **Guards run on every reply** | A prompt is guidance, not a guarantee. Numbers, guarantees and disclosures are checked in code after generation. |
| **Rule-based fallback brain** | Free-tier quotas and malformed JSON are routine. The call continues and the failure is logged, instead of breaking. |
| **Lexical retrieval, not embeddings** | No extra service, every score explainable, deterministic, regression-tested. Vector path documented for production. |
| **Browser calling, not telephony** | No provider account needed to run or review the prototype; the same `/agent/turn` pipeline would sit behind Twilio Media Streams. |
| **Caller's own words beat the model's summary** | Recorded calls showed the model dropping a "yes" and summarizing a disclosed heart condition as "none". |
| **Q3: different sectors and flows per market, not a translated script** | A translated qualification script is still a qualification script. PH renewal and ID installment reminders end in outcomes Q1 has no equivalent for. |
| **Q3: source documents in the language each market publishes in** | Philippine insurers publish in English while agents speak Taglish; that mismatch is real, and it is what makes PH retrieval hard. |
| **Q3: local voices run locally (MMS-TTS)** | No provider here offers a Filipino voice. Local models need no key and no quota, so Q3 speech is unaffected by Groq's daily limits. |
| **Q4: rules over the LLM for signals** | Measured: rules 1.00/1.00, +LLM 1.00/0.875 and ~215 ms slower. The LLM called "I paid it already" payment difficulty. |
| **Q4: every suppression is recorded with its reason** | Suppression is the hard part of a nudge system; without the reasons there is no false-positive analysis. |

## Where to look

| Concern | File |
|---|---|
| Q1 agent, guards, qualification | `app/agent.py` |
| Q3 market flows, scripts, compliance rewrites | `app/flows.py`, `app/reminder_agent.py` |
| Q2 ingestion and per-market vocabularies | `app/ingest.py`, `app/market_vocab.py` |
| Retrieval, expansions, stop words, gates | `app/kb.py` |
| Q4 signals, nudge suppression, streaming | `app/signals.py`, `app/nudges.py`, `app/stream.py` |
| Native voices | `app/local_tts.py` |
| Evidence scripts | `scripts/eval_retrieval.py`, `run_test_calls.py`, `asr_check.py`, `replay_call.py`, `eval_nudges.py` |

## Build order followed

1. Collect and clean the raw sources; publish traceable records.
2. Evaluate retrieval on a labelled set **before** connecting it to the agent.
3. Build the stateful agent on top of gated retrieval, with code-owned rules and guards.
4. Add the business action (mock CRM) and the live lead panel.
5. Record calls, replay each failure as a regression test, fix, re-verify.

## Production upgrades

- Hybrid retrieval: BM25 fused with dense embeddings plus a cross-encoder reranker, in pgvector.
- Durable storage (PostgreSQL) for sources, chunks, calls, leads and evaluation runs; Redis for sessions.
- Paid model tier with latency budgets, circuit breakers and per-turn tracing (OpenTelemetry).
- Streaming speech with barge-in; telephony adapter.
- Authentication, consent capture, encryption and retention rules before any real caller data.
- The labelled retrieval set and the scripted call suite run in CI, so quality and safety cannot regress.
