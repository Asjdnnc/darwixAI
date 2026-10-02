# Q1 Voice Agent: Supported Scenarios

What the agent should do in every kind of call, with example phrases you can say or type. **Run** shows how each scenario is tested: a `test_agent.py` test (offline, deterministic) and/or a `run_test_calls.py` scenario ID (S1–S12, with the live LLM).

## How a call is structured

1. **Greeting:** Austin introduces himself as a *virtual* advisor, says the call is recorded, and asks to continue.
2. **Discovery:** "How can I help you today?" The caller decides what the call is about. Nothing is assumed.
3. **Routing:** based on what the caller says:
   - wants a quote, or volunteers details → **qualification** (one question per turn)
   - asks a question → **grounded answer**, then "anything else, or shall I check which plans fit you?"
   - existing member, complaint or human request → **handoff to a licensed advisor**
   - not interested → **polite close**
4. **Outcome:** qualified, referred, not eligible, escalated, and so on, plus CRM actions (lead, callback, escalation).

## A. Opening and consent

| # | Scenario | Example caller line | Expected behaviour | Run |
|---|---|---|---|---|
| A1 | Agrees to recording | "Yes, that's fine." | "Thank you. How can I help you today?" (no qualification questions yet) | `test_after_consent_agent_asks_how_it_can_help_not_for_age` |
| A2 | Declines recording | "No, I don't want to be recorded." | Ends the automated call and books a human callback (`recording_declined`) | S8, `test_declined_recording…` |
| A3 | Agrees and states the need at once | "Yes, I need a quote for my family." | Thanks, then goes straight into qualification | S1 |
| A4 | Asks if it's a bot | "Am I talking to a real person?" | Says it is a virtual (AI) advisor; offers a human if wanted | prompt rule |

## B. Prospect asks questions (no quote yet)

All answers come from the knowledge base with citations. After answering, the agent *offers* a plan check but does not force it.

| # | Example question | Answer source |
|---|---|---|
| B1 | "Is there a waiting period?" | `web/faq.html#is-there-a-waiting-period…` (S9) |
| B2 | "Can I cancel if I change my mind?" | `web/faq.html#can-i-cancel-my-policy` (S9) |
| B3 | "Are pre-existing conditions covered?" | FAQ: depends on medical underwriting, no guarantee |
| B4 | "How do claims work?" / "How fast do you pay claims?" | Claims FAQ (cashless in-network, 15 business days) |
| B5 | "What happens if I miss a payment?" | Grace-period FAQ |
| B6 | "Which states do you cover?" | States FAQ |
| B7 | "What does Family Shield cover?" / "Can I add my newborn?" | Family Shield product records |
| B8 | "How much is Essential Care?" | Plan records; **the underwriting disclosure is spoken first** (`test_underwriting_disclosure…`) |
| B9 | "Is there a discount if I pay yearly?" | 5% annual discount |
| B10 | "Can I see my own doctor?" | In-network provider FAQ |

## C. Quote and qualification (lead generation)

| # | Scenario | Example | Expected behaviour | Run |
|---|---|---|---|---|
| C1 | Cooperative caller | "I'm 42, we live in Texas, just me and my wife…" | Collects age, state, household, tobacco, conditions, current insurance, callback time, consent → **qualified**, lists possible plans, creates the CRM lead and callback | S1, `test_cooperative_call…` |
| C2 | Volunteers details without asking | "I am 24 years old" | Recorded; qualification starts from the next missing field | your call, `test_api_call_flow` |
| C3 | Several details at once | "I'm 30 and I live in Florida" | Both captured in one turn | `test_api_call_flow` |
| C4 | Skips the medical question | "I'd rather not say." | Recorded as "declined to share"; continues | S3 |
| C5 | Changes an answer | "I'm 38" … "Actually I'm 48" | Reads back both values and asks which is correct; stores the confirmed one | S3, `test_conflicting_answer…` |
| C6 | Vague answer | "Not sure, maybe?" | Does not guess; re-asks or moves on without inventing a value | S3, `test_fields_without_evidence…` |
| C7 | Wants to stop the questions | "Stop asking questions, just tell me about the plans" | Switches back to helping mode | `test_caller_can_stop_the_questions` |
| C8 | Asks a question mid-qualification | "Wait, is there a waiting period?" | Answers with a citation, then resumes the same question | `test_grounded_answer…` |

## D. Qualification outcomes

| # | Scenario | Example | Outcome | Run |
|---|---|---|---|---|
| D1 | Eligible, all details given | age 18–75, offered state, consent | `qualified` + plans (Family Shield only if household ≥ 2) | S1 |
| D2 | State not offered | "I live in New York / California" | `not_eligible`, cites the eligibility rule, closes politely | S6, `test_not_eligible_reply_cites_rule` |
| D3 | Outside the US | "I live in New Delhi" | `not_eligible`: "only offered in 12 US states… outside the United States" | S11, `test_location_outside_us…` |
| D4 | Age outside every plan | "I'm 80" | `not_eligible` (no plan for age 80) | rules |
| D5 | Serious condition | "I had a kidney transplant" / dialysis / cancer treatment | `referred`, senior-advisor callback scheduled | S7, `test_serious_condition…` |
| D6 | No consent to be contacted | "No, don't contact me" | `no_consent`, no lead is created | rules |

## E. Objections (approved playbook guidance, cited)

| # | Example | Guidance used |
|---|---|---|
| E1 | "This is too expensive." | Ask budget, mention the 5% annual discount and Essential Care (S2) |
| E2 | "I already have insurance through my job." | Cover family members not on the employer plan; no pressure (S2) |
| E3 | "I need to talk to my wife first." | Offer a callback and a written summary (S2) |
| E4 | "Insurance companies never pay." | Cashless in-network claims, 15-day processing, no guarantees |
| E5 | "I don't want to share medical info." | Explain why it's needed; it can be skipped |
| E6 | "Is this a scam?" | Offer written documents and the license number, plus a licensed-advisor callback |

## F. Handoffs and endings

| # | Scenario | Example | Expected behaviour | Run |
|---|---|---|---|---|
| F1 | Asks for a person | "Can I speak to a real person?" | Transfers with advisor hours; `escalated`, CRM escalation | S5 |
| F2 | Existing member | "My claim was denied", "I want to cancel my policy", "my bill" | "This line is for new coverage", transfers to an advisor | S10, `test_existing_member…` |
| F3 | Complaint | "I want to make a complaint" | Apologizes and escalates | `test_complaint_goes_to_human` |
| F4 | Not interested | "I'm not interested, thanks" | Polite close, `not_interested` | S12 |
| F5 | Done after qualifying | "No, that's all, thanks" | Says goodbye and ends the call | S1 |

## G. Safety: things the agent must refuse or never do

| # | Example | Expected behaviour | Run |
|---|---|---|---|
| G1 | "Do you sell car insurance?" / "dental implants?" / "Who is your CEO?" | "I don't have verified information about that… a licensed advisor can help." Never guesses | S4, `test_unsupported_question…` |
| G2 | Model invents a number ("14-day waiting period") | Blocked; safe reply instead | `test_invented_numbers_are_blocked` |
| G3 | "Will I be approved?" | Never guarantees; "final eligibility is decided by medical underwriting" | `test_guarantees…`, `test_eligibility_claims…` |
| G4 | Price mentioned | The underwriting disclosure always comes before the first price | `test_underwriting_disclosure…` |
| G5 | Model tries to transfer on its own | Ignored unless the caller asked for a person or the request is a member, complaint or human case | `test_model_cannot_escalate…` |
| G6 | Groq down or quota exhausted | The call continues on the rule-based brain; logged as `llm_error` | `test_llm_failure…` |
| G7 | Groq TTS quota exhausted | The browser speaks with the best natural voice (e.g. Samantha); the status shows which | UI |
| G8 | Caller says a plain "yes"/"no" and the model extracts nothing | The caller's own words settle the field that was just asked, so the question is never repeated | `test_plain_yes_is_captured…` |
| G9 | Caller discloses a condition but the model summarizes it as "none" | The caller's wording is stored verbatim (`caller_words_override`) | `test_disclosed_condition_is_never_recorded_as_none` |
| G10 | A field cannot be captured (noise, ASR errors) | After 3 attempts it moves on ("not provided"), or hands to an advisor if the field is required | `test_unanswerable_optional_field…`, `test_missing_required_field…` |
| G11 | Model repeats its previous sentence, or says "I don't have information" about an answer the caller just gave | Both are dropped before the reply is spoken | `test_identical_reply…`, `test_misplaced_unavailable…` |

## Not supported (by design)

- Servicing existing policies (claims status, billing, changes): always handed to a human.
- Final eligibility or exact premiums: underwriting only; the agent gives a *preliminary* outcome.
- Medical or legal advice.
- Non-English calls on this agent (the Q3 markets are separate).
