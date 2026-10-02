# Question 1: Knowledge-Grounded Voice Agent (Health-Insurance Lead Qualification)

**Use case:** inbound health-insurance lead qualification for the synthetic insurer Darwix Health. The virtual advisor "Austin" discloses that the call is recorded, answers questions using only the Q2 knowledge base, handles objections with the approved playbook, collects the qualification details, gives a preliminary outcome, and hands off to a human advisor via the mock CRM.

## Calling interface

The web calling interface is at `http://127.0.0.1:8000/`. The browser records microphone audio and detects silence, then sends it to `/voice/transcribe` (Groq Whisper). The text goes to `/agent/turn`, and the reply is spoken through `/voice/speak` (Groq Orpheus TTS, with browser speech synthesis as a fallback). A text box allows typed turns. The right-hand panel shows the live lead record, the outcome, conflicts, and CRM actions.

## Architecture

```text
Browser mic ─► /voice/transcribe (Whisper) ─► /agent/turn ─► LeadQualificationAgent.turn()
                                                               │
   1. deterministic intent checks (human request, recording refusal)
   2. Q2 retrieval: kb.retrieve(text, grounded_only=True)  ── BM25 + coverage gates, cited chunks
   3. brain: Groq JSON call {reply, citations, caller_intent, extracted fields}
              └─ fallback_brain (rules) if Groq is unavailable or errors
   4. merge_fields: evidence check per field, conflicts are read back rather than overwritten
   5. evaluate_outcome: data/rules/qualification_rules.json (rules cite KB records)
   6. CRM actions: lead upsert, callback scheduling, escalation (+ optional webhook)
   7. guard_reply: unsupported question, invented numbers, prohibited promises,
                   underwriting disclosure, next question appended by code
                                                               │
Browser speaker ◄─ /voice/speak (TTS) ◄── reply + citations + lead state
```

**Design principle: the LLM writes the language; code owns the decisions.** Eligibility, escalation, compliance wording and question order are deterministic, so they are testable and cannot be talked around. The model handles understanding, extraction and natural phrasing.

## Conversation flow

| Stage | Behaviour | Source |
|---|---|---|
| Greeting | Introduces itself as a *virtual* advisor and discloses recording, then asks permission | `kb_policy_001` |
| Discovery | "How can I help you today?" The caller chooses: questions get grounded answers plus an offer to check plans; a quote request or volunteered details start qualification; existing-member requests and complaints go to a human; "not interested" closes politely | `kb_process_001` |
| Recording consent | A refusal ends the automated call and books a human callback | `kb_policy_001` |
| Qualification | One question per turn in the playbook order: age, state, household size, tobacco, medical conditions (may be skipped), current insurance, callback time, consent to contact | `kb_qualification_003`, rules file |
| Questions and objections | Answered first from retrieved, cited records, then the next qualification question | Q2 knowledge base |
| Price | The underwriting disclosure is spoken before the first price (code enforces it) | `kb_policy_001` |
| Outcome | `qualified` / `qualified_partial` / `referred` / `not_eligible` / `no_consent` / `escalated` / `recording_declined` | `kb_qualification_001/002` |
| Close | Possible plans are summarized, an advisor callback is confirmed, and the call ends | |

## Qualification logic (`evaluate_outcome`)

Rules live in `data/rules/qualification_rules.json`, and each rule cites the record it implements:

- **Referred**: medical conditions mention cancer treatment, dialysis, a transplant, or a current hospital admission. A senior health advisor callback is scheduled.
- **Not eligible**: the state is not offered (e.g. California or New York), or no plan matches the age. The agent explains the reason, cites it, and closes.
- **Qualified**: the age fits at least one plan, the state is offered, and the caller consents to contact. Possible plans are computed (Family Shield needs at least 2 people). The result is `qualified` when every field is collected and `qualified_partial` otherwise.
- The agent always calls this a *preliminary* outcome. Final eligibility belongs to underwriting.

## Grounding and safety guards

| Risk | Control |
|---|---|
| Hallucinated policy or price facts | Only gated knowledge-base chunks reach the model; it must cite excerpt indexes, which are mapped back to `source_ref`. |
| Answering a question with no evidence | If the caller asked a question (both the model and a surface check agree) and retrieval returned nothing, the reply is replaced with "I don't have verified information… a licensed advisor can help". |
| Invented numbers | Every number in a reply must appear in the retrieved evidence, the caller's own words, or the lead record; otherwise the safe reply is used. |
| Prohibited promises | Sentences that guarantee approval or coverage, or tell the caller they are eligible or qualified, are replaced with the compliant statement (cites `kb_policy_002`). Negated forms ("I can't guarantee…") are kept. |
| Missing disclosure | The underwriting disclosure is prepended to the first reply containing a price. |
| Invented caller details | An extracted field is accepted only if it answers the question just asked or the caller's words contain evidence for it. |
| Conflicting details | A changed age, state, household size or tobacco answer is not overwritten; both values are read back and the caller confirms. |
| Unwanted escalation | Only an explicit request for a person escalates; the model cannot transfer on its own initiative. |
| Model drops or mis-summarizes an answer | For the field just asked, a rule-based reading of the caller's words wins over the model's value (`caller_words_override`), so a plain "yes" is never lost and a disclosed condition is never stored as "none". |
| Looping the same question | After 3 attempts a field is abandoned as "not provided", or the call goes to an advisor when the field is required for the outcome. |
| Repeated or misplaced replies | A reply identical to the previous turn is dropped, as is an "I don't have verified information" response to a statement rather than a question. |
| Provider outage, rate limit or invalid JSON | Retries, then the rule-based brain continues the call. Every intervention is logged in `guard_events`. |

## Business action (mock CRM)

`app/crm.py` writes `data/crm/leads.json`, `callbacks.json` and `escalations.json`, and POSTs each event to `CRM_WEBHOOK_URL` if it is set. A lead is created once the caller consents and is updated on every turn with the fields, possible plans, outcome and a CRM summary. A callback is scheduled with the caller's preferred time (with a senior advisor when the lead is referred). The records are available at `GET /crm/leads|callbacks|escalations`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /calls/start?market=en` | New call and greeting (with recording disclosure) |
| `POST /agent/turn` `{call_id, customer_text}` | One caller turn; returns `text, citations, lead, conflicts, outcome, actions, end_call, escalated` |
| `GET /calls/{id}` | Full session state and transcript |
| `POST /calls/{id}/end` | Finalise the call; returns the CRM summary |
| `GET /retrieve?q=` | Operator search of the knowledge base |

The full scenario catalogue, with example phrases, is in `docs/Q1_SCENARIOS.md`.

Every call is saved to `test_logs/calls/<call_id>.json` after each turn, including the transcript with per-turn citations, the lead, the outcome, actions, and guard events.

## Testing

- `pytest`: 126 offline tests across all four questions. A scripted fake LLM exercises every guard deterministically: grounded citations, unsupported questions, invented numbers, guarantees, the disclosure, conflicts, escalation, recording refusal, ineligibility, referral, LLM failure, and invented extractions.
- `python scripts/run_test_calls.py`: 8 scripted calls through the live Groq model covering every scenario the assessment requires (cooperative, objection, incomplete or conflicting, out-of-scope, human request) plus ineligibility, referral, and recording refusal. The results go to `docs/Q1_TEST_CALLS.md`.
- Voice recordings: run the browser UI and record the screen with audio for at least 3 of these scenarios.

## Known limitations

- Questions run one per turn, and the latency is one LLM round trip plus TTS (about 1–3 s on Groq). Streaming TTS and barge-in would make it feel more natural.
- The number guard does not catch numbers written as words ("thirty days"); the prompt asks for digits.
- Sessions are held in memory (with JSON snapshots on disk); production would use Redis or Postgres.
- The current UI is a turn-based web call rather than telephony; a Twilio Media Streams adapter could feed the same `/agent/turn` pipeline.
