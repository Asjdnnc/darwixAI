# Recorded Call Evidence — Question 1

Four recorded browser calls with the health-insurance lead-qualification voice agent, each with
audio and the transcript the UI saved on "End Demo". Together they cover the assessment's required
test coverage. Full per-turn server records, including citations, lead state, CRM actions and every
safety guard that fired, are in `test_logs/calls/<call_id>.json`.

| File | Scenario | Outcome | Required coverage |
|---|---|---|---|
| `call1.mp3` / `call1.json` | Cooperative customer: couple seeking coverage, all details given | **qualified** — Essential Care / Family Shield; CRM lead + callback created | Cooperative customer |
| `call2.mp3` / `call2.json` | Prospect asks questions first: waiting period, cancellation | Both answered from the knowledge base **with citations**; caller ends the call | Grounded answers, caller-led flow |
| `call3.mp3` / `call3.json` | Dental implants, car insurance, then "can I speak to a real person?" | States the information is unavailable and **invents nothing**, then **escalated** with advisor hours | Out-of-scope question; bot states when information is unavailable; human-assistance request |
| `call4.mp3` / `call4.json` | Quote request, caller corrects their own age, then price and spouse objections | Conflicting age **read back and resolved** (38 → 48); objections answered from the approved playbook | Objection; incomplete / conflicting details |
| `Darwix.mp4` | Screen recording of the call UI | Shows live citations, the lead panel filling in, and the outcome badge | — |

## Notes on these recordings

- `call4` was recorded while the Groq free-tier daily token limit was exhausted, so several turns fell
  back to the rule-based brain and read as clipped. The outcome logic, conflict read-back and objection
  handling are unaffected and visible in the transcript.
- Mixed voices in `call2` are the documented text-to-speech fallback: cached sentences play in the Groq
  voice, and sentences needing new audio after the daily speech quota was reached are spoken by the
  browser. The active engine is shown in the call UI and recorded in the logs.
- Three defects surfaced by these recordings were fixed and are now covered by regression tests in
  `tests/test_agent.py` (see "What the recorded calls taught us" in `docs/ASSESSMENT_REPORT.md`).
