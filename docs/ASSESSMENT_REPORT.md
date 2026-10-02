# AI Engineer Assessment — Report

A working health-insurance lead-qualification voice agent, grounded in a knowledge base built from messy business content. All source material is synthetic (the fictional insurer "Darwix Health") and contains no real customer data.

This report covers **all four questions**. Every number below is produced by a script in this
repository and can be re-run.

```bash
cp .env.example .env            # add your GROQ_API_KEY
pip install -e '.[dev]'
python -m app.ingest            # raw sources -> data/kb/records.jsonl
uvicorn app.main:app --reload   # http://127.0.0.1:8000/ for the voice call
pytest                          # 126 offline tests, no network or credentials needed
```

| Deliverable | Where |
|---|---|
| Architecture (all four questions) | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Architecture and design decisions | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Knowledge-base design | [KB_DESIGN.md](KB_DESIGN.md) |
| Retrieval evaluation (42 queries, verdicts) | [RETRIEVAL_EVAL.md](RETRIEVAL_EVAL.md) |
| Voice-agent design | [VOICE_AGENT.md](VOICE_AGENT.md) |
| Scenario catalogue | [Q1_SCENARIOS.md](Q1_SCENARIOS.md) |
| Scripted call transcripts + results | [Q1_TEST_CALLS.md](Q1_TEST_CALLS.md) |
| Recorded calls (audio + transcripts) | `Evidence/` and `Evidence/q3/` |
| Q3 markets: localization, ASR, native TTS | [Q3_MARKETS.md](Q3_MARKETS.md) |
| Q4 real time: signals, nudges, latency, false positives | [Q4_REALTIME.md](Q4_REALTIME.md) |

---

## Question 2 — Production-ready knowledge base

### Inputs

Ten raw sources in `data/raw/`, each registered in `manifest.json` with its origin, version, authority rank and the version it supersedes: four web pages, a lead-intake form, a spreadsheet export, two versions of an underwriting policy (as text extracted from PDFs), a sales playbook, and a scanned PDF. Each carries realistic defects on purpose — cookie banners, navigation, marketing copy, a customer testimonial containing a name and member ID, a repeated FAQ, a duplicate table row, a negative deductible, an impossible date ("February 30"), words hyphenated across PDF line breaks, inconsistent terminology ("deductable", "OOP max", "monthly fee", "pre existing disease"), four different date formats, and a supervisor's name, email and phone.

### Pipeline (`app/ingest.py`, standard library only)

Extract → clean → protect → normalize → validate → deduplicate → publish. Every decision is recorded in `data/kb/ingest_report.json`.

| Requirement | Result in the current run |
|---|---|
| Remove navigation, headers, footers, repeated content | Cookie banners, menus, footers, running PDF headers and "Page x of y" removed; **7** irrelevant sections dropped |
| Handle extraction failures | The scanned PDF has no text layer; it is **flagged and excluded**, never silently indexed |
| Flag obvious source errors | **2** sections quarantined (negative deductible; February 30) |
| Remove duplicate / near-duplicate information | **2** records and **4** sentences dropped, each logged with the source kept and why |
| Standardize headings, dates, terminology, form fields | **14** terminology rewrites, 4 date formats → ISO 8601, 12 form labels ("DOB", "Phone #", "Smoker?") → canonical field names |
| Identify and protect PII | Name, email and phone redacted; every published record asserts `pii: false` |
| Versioning | Underwriting policy v1 is superseded by v2 and excluded entirely |

**Output: 30 records** — product 9, FAQ 7, objection 6, qualification 4, policy 3, process 1 — split into 32 chunks (largest 72 words).

### Schema and traceability

Each record carries `record_id`, title, cleaned content, category and subcategory, `source_ref` (raw file + section anchor), `source_type`, `source_origin`, `version`, `effective_date`, `pii` / `pii_redacted`, canonical `terms`, and a `content_hash` for change detection. The `source_ref` is what the agent cites, so every customer-facing sentence traces to a specific section of a specific source.

### Retrieval

BM25 over stemmed tokens with the record title weighted ×4, a small query-expansion table mapping caller phrasing onto knowledge-base vocabulary ("real person" → human advisor, "retired" → senior), and a **coverage** factor: the IDF-weighted share of the question a chunk actually explains. Words never seen in the corpus ("car", "planet", "CEO") carry maximum IDF, so a generic overlap on "insurance" cannot ground an answer.

Two gates decide whether a result may be used in a customer-facing answer (score ≥ 2.5, coverage ≥ 0.4). Below them the agent receives nothing and must say the information is unavailable. `/retrieve` is ungated for operator search.

### Results ([RETRIEVAL_EVAL.md](RETRIEVAL_EVAL.md))

42 labelled queries covering product, policy, qualification, FAQ, objection and out-of-scope, each with the retrieved record, source reference, relevance explanation and a verdict.

| | Tuning split (28) | Held-out split (14) |
|---|---|---|
| Correct record ranked first | 100% | **82%** |
| Out-of-scope questions rejected | 4/4 | 3/3 |

The tuning split was used to choose expansions, weights and gates, so **82% on the held-out split is the honest figure**. Both failures are reported rather than hidden: one needs numeric reasoning over age ranges (that belongs in the agent's qualification logic, not retrieval), and one ranks the right FAQ first but falls below the coverage gate, so the agent escalates instead of answering — a safe failure, not a hallucination.

### Deliberate choice: lexical, not vector

Ranking is BM25 rather than embeddings. It needs no extra dependency or service, every score is explainable, it is fully deterministic, and it is covered by regression tests that fail if quality drops. The production path — dense embeddings fused with BM25, plus a cross-encoder reranker — is specified in [KB_DESIGN.md](KB_DESIGN.md) and would likely fix both held-out misses.

---

## Question 1 — Knowledge-grounded voice agent

**Use case: health-insurance lead qualification.** A browser voice call at `/`: microphone audio with silence detection → Groq Whisper → the agent → Groq Orpheus speech, with a typed-text fallback and a live lead panel.

### Design principle

**The model writes the language; code owns the decisions.** Eligibility, escalation, compliance wording, question order and outcomes are deterministic and unit-tested, so they cannot be talked around by the model or the caller. The LLM handles understanding, extraction and natural phrasing.

### Flow

The agent opens by identifying itself as a *virtual* advisor and disclosing recording. It then asks **"How can I help you today?"** rather than assuming the caller wants a quote. From there it routes on what the caller actually says: questions get grounded, cited answers; a quote request (or volunteered details) starts qualification; existing-member requests and complaints go to a human; "not interested" closes politely.

Qualification collects age, state, household size, tobacco use, medical conditions (skippable), current insurance, callback time and consent — one question per turn, in the order given by the playbook record.

### Qualification logic

Rules live in `data/rules/qualification_rules.json`, each citing the knowledge-base record it implements. Outcomes: `qualified` / `qualified_partial` (with matching plans), `referred` (cancer treatment, dialysis, transplant, current admission), `not_eligible` (state not offered, or no plan for the caller's age), `no_consent`, `escalated`, `recording_declined`, `not_interested`. The agent always presents this as a **preliminary** outcome; final eligibility belongs to underwriting.

### Grounding and safety controls

Every control is enforced in code and covered by tests, not left to prompt compliance:

| Risk | Control |
|---|---|
| Hallucinated facts | Only gated knowledge-base chunks reach the model; cited excerpt indexes map back to `source_ref` |
| Answering without evidence | A question with no retrieved evidence returns "I don't have verified information… a licensed advisor can help" |
| Invented numbers | Every figure must appear in the evidence, the caller's own words (including numbers spoken as words), or the lead record |
| Prohibited promises | Guarantees of approval, coverage or eligibility are rewritten to the compliant statement, citing the policy; negated forms are preserved |
| Missing disclosure | The underwriting disclosure is prepended to the first reply containing a price |
| Invented caller details | A field is accepted only if it answers the question asked or the caller's words support it |
| Conflicting answers | A changed age, state, household size or tobacco answer is read back for confirmation, never silently overwritten |
| Unwanted escalation | Only the caller can trigger a handoff; the model cannot transfer on its own initiative |
| Irreversible outcome on a bad transcript | A location outside the US is confirmed once before the call is ended |
| Provider outage, rate limit, malformed JSON | Retries, prose recovery, then a rule-based brain; the call never breaks. Every intervention is logged in `guard_events` |

### Business action

A mock CRM (`app/crm.py`) writes `data/crm/leads.json`, `callbacks.json` and `escalations.json`, and POSTs each event to `CRM_WEBHOOK_URL` when configured. A lead is created once the caller consents and updated every turn with the collected fields, possible plans, outcome and a written summary; callbacks are scheduled with the caller's preferred time (a senior advisor when the lead is referred). Exposed at `GET /crm/{leads|callbacks|escalations}`.

### Evidence

**Recorded calls** (`Evidence/`, audio + JSON transcripts):

| Call | Scenario | Outcome |
|---|---|---|
| `call1` | Cooperative customer, couple seeking cover | **qualified** — Essential Care / Family Shield, CRM lead + callback created |
| `call2` | Prospect asks questions first (waiting period, cancellation) | Both answered with citations; caller ends the call |
| `call3` | Out-of-scope and unsupported questions, then asks for a person | States information is unavailable, invents nothing, then **escalated** |
| `call4` | Quote, corrects their own age, then raises price and spouse objections | Conflicting age **read back and resolved**; objections answered from the approved playbook |

Together these cover the required test coverage: cooperative customer, objection, incomplete/conflicting details, out-of-scope question, human-assistance request, and the bot stating when information is unavailable.

**Scripted calls** — `python scripts/run_test_calls.py` drives 12 scenarios through the live model and the full pipeline, writing transcripts to `test_logs/calls/` and results to [Q1_TEST_CALLS.md](Q1_TEST_CALLS.md). Beyond the four above, these cover ineligibility, referral to a senior advisor, declined recording, existing-member requests, a caller outside the US, and "not interested".

**Automated tests** — `pytest`: 70 tests, fully offline (no credentials, no network). A scripted fake model exercises each safety control deterministically, and retrieval quality is a regression test that fails if either evaluation split degrades.

Every call is persisted per turn to `test_logs/calls/<call_id>.json` with the transcript, citations, lead state, outcome, CRM actions and guard events.

---

## Question 3 — Native-language voice bots

Two bots built as different products, not one script translated twice.

| | 🇵🇭 Philippines | 🇮🇩 Indonesia |
|---|---|---|
| Sector / flow | Life insurance, renewal + lapse reminder | Multifinance, installment reminder + collections |
| Language | Taglish | Bahasa, formal and colloquial |
| Records | 30 | 32 |
| Native voice | `mms-tts-tgl`, local | `mms-tts-ind`, local |
| ASR | Groq Whisper `tl` | Groq Whisper `id` |

Outcomes have no Q1 equivalent — promise to pay, already paid, hardship referral, dispute, refusal,
wrong person — and each market carries its own compliance rules: Philippine grace-period wording and
reinstatement promises, Indonesian OJK collections conduct.

**The main technical problem was cross-lingual retrieval.** Philippine insurers publish in English
while clients speak Taglish, so "Magkano po ang babayaran ko?" shares almost no tokens with
"Premiums may be paid annually". Baseline retrieval scored 2/5. Three measured fixes — per-market
query expansion, per-market stop words (without which Taglish function words made every question
match whichever record was written in the caller's language), and per-market gates — took it to
**7/7 per market**, with every out-of-scope question rejected in both languages.

**ASR was measured, not assumed.** Indonesian mean WER 0.056 with 13/13 finance terms retained;
Javanese transcribes perfectly while Sundanese loses word boundaries. Philippine mean WER 0.293 with
4/12 terms retained: Filipino words transcribe well, the English loanwords the business depends on
do not. The system absorbs this because it depends on term retention rather than perfect
transcription.

Four end-to-end calls are in `Evidence/q3/`, with the caller's voice synthesized (the author speaks
neither language) and real ASR errors left visible.

## Question 4 — Live insights and nudges

Agent assist watching a **human** agent on a call in progress. A recording is replayed at wall-clock
speed — 92.3 s of wall clock for 92 s of audio — so nothing is analysed after the fact.

| Stage | P50 | P95 |
|---|---|---|
| ASR (Groq Whisper) | 297.8 ms | 371.6 ms |
| Signal extraction | 0.0 ms | 1.7 ms |
| Nudge generation + delivery | 0.1 ms | 0.3 ms |
| **End to end** | **298.0 ms** | **371.7 ms** |

ASR is effectively the whole budget; everything after it is free by comparison.

Suppression is the hard part, so every control is implemented and every suppression is recorded with
its reason: confidence threshold, cooldown, duplicate suppression, topic grouping, priority, expiry
and a repetition limit. Compliance is deliberately exempt from grouping, because two compliance
findings are two distinct regulatory events.

On 30 labelled chunks, 16 of them adversarial, the tightened rules score **recall 1.00, precision
1.00, false-positive rate 0.00**. The LLM second pass was measured against the same set and turned
**off**: it added no recall, cost precision (0.875), and called "I paid it already, last Tuesday"
payment difficulty.

## What the recorded calls taught us

Each recording was replayed and turned into a regression test. Real failures found and fixed this way:

- A plain "yes" was sometimes dropped by the model, looping the consent question. The caller's own words now settle the field that was just asked.
- A disclosed heart condition was summarized by the model as "none" — a dangerous value on a lead record. Caller wording is now stored verbatim.
- Transcription turned "Ohio" into "I.O", which was briefly accepted as a foreign location and ended the call. Place names now require real words, and a non-US location is confirmed before any call-ending decision.
- Objections were counted as failures to answer, so a caller raising two objections was escalated with "could not capture consent".
- "Ten Thousand" was treated as an invented number because it was spoken as words.

## Known limitations

- **Q4 signals are English-only.** The Q3 markets would need their own rule sets; the structure supports it, the rules are not written.
- **No native-speaker review** of the Taglish or Bahasa content, which in a collections context is a compliance risk rather than a style issue.
- **Retrieval is lexical.** Paraphrases with no shared vocabulary can fall below the coverage gate; the agent escalates rather than guessing.
- **Free-tier quotas are a real constraint.** Groq's daily limits (200k chat tokens, 3,600 speech tokens) were exhausted during testing. The agent degrades to rule-based replies and the browser's voice, which is visible in the UI and the logs, but some recordings show the clipped fallback style rather than the model's natural phrasing.
- **Speech is English-only** on this agent; Philippines and Indonesia are the Q3 scope.
- **Sessions are in memory** with JSON snapshots on disk; production needs Redis or Postgres.
- **Turn-based web calling**, not telephony. A Twilio Media Streams adapter would feed the same `/agent/turn` pipeline.
- **No authentication** on the API or CRM endpoints; this is a prototype, not a deployment.

## Production plan

Hybrid retrieval (BM25 + embeddings + reranker) in pgvector; durable storage for sources, chunks, calls, leads and evaluations; a paid model tier with latency budgets and circuit breakers; streaming speech with barge-in; authentication, consent capture and retention rules; OpenTelemetry tracing per turn; a labelled evaluation set in CI so retrieval and safety controls cannot regress; and human review of every compliance-related signal before it reaches a customer.
