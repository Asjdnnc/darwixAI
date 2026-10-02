# Architecture

Scope: **Question 1** (knowledge-grounded voice agent) and **Question 2** (production-ready knowledge base). All content is synthetic; the fictional insurer is "Darwix Health".

## System

```text
┌─ BROWSER (app/static/index.html) ────────────────────────────────────────────┐
│  microphone + silence detection          chat, citations, live lead panel     │
└───────────┬──────────────────────────────────────────────▲──────────────────┘
            │ audio                                        │ reply + citations
            ▼                                              │ + lead / outcome
┌─ FastAPI (app/main.py) ──────────────────────────────────┴──────────────────┐
│  POST /voice/transcribe   Groq Whisper                                       │
│  POST /calls/start        open session, greet, disclose recording            │
│  POST /agent/turn    ───► LeadQualificationAgent.turn()  (app/agent.py)      │
│  POST /voice/speak        Groq Orpheus TTS + per-sentence disk cache         │
│  GET  /retrieve           ungated operator search                            │
│  GET  /crm/{kind}         leads · callbacks · escalations                    │
└──────────────────────────────────────────────────────────────────────────────┘

ONE TURN (app/agent.py)
  caller text
    │
    1. deterministic intent checks ......... human request? recording refused?
    2. RETRIEVE (Q2) ....................... kb.retrieve(text, grounded_only=True)
    │                                        BM25 + coverage gates -> cited chunks
    3. BRAIN ............................... Groq JSON: reply, citations, intent,
    │                                        extracted fields
    │                                        ├─ prose recovery if JSON invalid
    │                                        └─ rule-based brain if provider fails
    4. MERGE FIELDS ........................ caller's own words win; changed answers
    │                                        become conflicts to read back
    5. RULES ............................... data/rules/qualification_rules.json
    │                                        -> qualified / referred / not_eligible /
    │                                           escalated / no_consent / ...
    6. BUSINESS ACTION ..................... mock CRM lead, callback, escalation
    │                                        (+ optional CRM_WEBHOOK_URL)
    7. GUARDS .............................. unsupported question · invented numbers ·
    │                                        prohibited guarantees · disclosure before
    │                                        price · repeated reply · next question
    ▼
  reply + citations + lead state       (persisted per turn to test_logs/calls/)
```

## Knowledge base build (offline, `python -m app.ingest`)

```text
data/raw/**  (web pages · PDF text · forms · CSV · markdown · scanned PDF)
   │  manifest.json: origin, version, authority rank, supersedes
   ▼
EXTRACT ──► CLEAN ──► PROTECT ──► NORMALIZE ──► VALIDATE ──► DEDUPE ──► PUBLISH
 per type   strip nav,  redact     headings,     quarantine   near-dup    assign
 parsers    footers,    PII        dates (ISO),  impossible   records &   record_id
 (failures  boilerplate            terminology,  dates,       sentences;
  flagged)                         form fields   negatives    authority wins
   │
   ▼
data/kb/records.jsonl   30 cited records ──► KnowledgeBase.upsert() ──► 32 chunks
data/kb/ingest_report.json   every decision, with reasons
```

Retrieval is BM25 over stemmed tokens (title weighted ×4) plus query expansion and a coverage
factor, with score/coverage gates deciding whether a chunk may ground a customer-facing answer.
See [KB_DESIGN.md](KB_DESIGN.md) and [RETRIEVAL_EVAL.md](RETRIEVAL_EVAL.md).

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
