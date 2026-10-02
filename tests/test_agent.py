import pytest
from fastapi.testclient import TestClient

from app.agent import GREETING, LeadQualificationAgent, fallback_brain, regex_extract
from app.crm import MockCRM
from app.kb import KnowledgeBase
from app.llm import GroqService
from app.main import app
from app.seed import seed


@pytest.fixture(scope="module")
def kb():
    base = KnowledgeBase()
    seed(base)
    return base


class ScriptedBrain:
    """Returns canned LLM outputs so guards can be tested deterministically."""

    def __init__(self, replies):
        self.replies = list(replies)

    def converse(self, session, text, context, expected, rules):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        base = fallback_brain(session, text, context, expected)
        return {**base, **reply}


def make(kb, tmp_path, brain=None):
    return LeadQualificationAgent(kb, brain or GroqService(), MockCRM(tmp_path / "crm"), tmp_path / "calls")


def run(agent, turns, call_id="c"):
    agent.start(call_id)
    return [agent.turn(call_id, t) for t in turns]


# --- core call flow

def test_greeting_discloses_recording(kb, tmp_path):
    first = make(kb, tmp_path).start("c")
    assert "recorded for quality and compliance" in first["text"] and first["text"] == GREETING


def test_cooperative_call_qualifies_and_creates_crm_records(kb, tmp_path):
    agent = make(kb, tmp_path)
    out = run(agent, ["yes", "I'm 35", "Texas", "family of 4", "no I don't smoke", "none", "no",
                      "weekday evenings", "yes"])[-1]
    assert out["outcome"]["status"] == "qualified"
    assert set(out["outcome"]["plans"]) == {"Essential Care", "Family Shield"}
    assert {a["type"] for a in out["actions"]} == {"lead", "callback"}
    leads = agent.crm.list("leads")
    assert leads[0]["fields"]["state"] == "TX" and leads[0]["status"] == "qualified"
    assert (tmp_path / "calls" / "c.json").exists()


def test_grounded_answer_is_cited_and_not_escalated(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "How long is the waiting period?"])[-1]
    assert out["citations"] == ["web/faq.html#is-there-a-waiting-period-before-my-coverage-starts"]
    assert out["escalated"] is False and "30-day" in out["text"]


def test_conflicting_answer_is_read_back_then_resolved(kb, tmp_path):
    agent = make(kb, tmp_path)
    outs = run(agent, ["yes", "I'm 35", "Texas", "Actually I'm 45"])
    assert outs[-1]["conflicts"] == {"age": {"previous": 35, "new": 45}}
    assert "which one is correct" in outs[-1]["text"].lower()
    resolved = agent.turn("c", "45")
    assert resolved["conflicts"] == {} and resolved["lead"]["age"] == 45


def test_human_request_escalates_with_cited_hours(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "Can I speak to a real person?"])[-1]
    assert out["escalated"] and out["end_call"] and out["outcome"]["status"] == "escalated"
    assert out["citations"] == ["docs/underwriting_policy_v2.txt#human-escalation"]
    assert any(a["type"] == "escalation" for a in out["actions"])


def test_model_cannot_escalate_on_its_own(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Thanks.", "intent": "provide_info"},
                           {"reply": "Let me transfer you.", "intent": "human_request"}])
    out = run(make(kb, tmp_path, brain), ["yes", "I had a kidney transplant"])[-1]
    assert not out["escalated"]


def test_declined_recording_ends_call_and_books_human_callback(kb, tmp_path):
    out = run(make(kb, tmp_path), ["No, I don't want to be recorded"])[-1]
    assert out["end_call"] and out["outcome"]["status"] == "recording_declined"
    assert [a["type"] for a in out["actions"]] == ["callback"]


def test_state_not_offered_is_not_eligible(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "I'm 33", "I live in California"])[-1]
    assert out["outcome"]["status"] == "not_eligible" and out["end_call"]
    assert "california" in out["text"].lower()


def test_serious_condition_is_referred(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "I want a quote", "I am 52", "Georgia", "just me", "no",
                                   "I had a kidney transplant"])[-1]
    assert out["outcome"]["status"] == "referred"


# --- safety guards

def test_unsupported_question_says_unavailable_even_if_model_answers(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"},
                           {"reply": "Yes, car insurance is $50 a month.", "intent": "out_of_scope"}])
    out = run(make(kb, tmp_path, brain), ["yes", "Do you sell car insurance?"])[-1]
    assert "don't have verified information" in out["text"] and "$50" not in out["text"]


def test_invented_numbers_are_blocked(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"},
                           {"reply": "The waiting period is 14 days.", "intent": "ask_question", "citations": [0]}])
    out = run(make(kb, tmp_path, brain), ["yes", "How long is the waiting period?"])[-1]
    assert "14" not in out["text"] and out["guard_events"][-1]["guard"] == "ungrounded_number"


def test_guarantees_are_replaced_but_negations_kept(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"},
                           {"reply": "Don't worry, you will be approved. How old are you?"},
                           {"reply": "I can't guarantee approval. Which state?"}])
    agent = make(kb, tmp_path, brain)
    out = run(agent, ["yes", "Will I be approved?"])[-1]
    assert "you will be approved" not in out["text"].lower() and "can't guarantee" in out["text"]
    assert "docs/underwriting_policy_v2.txt#prohibited-statements" in out["citations"]
    kept = agent.turn("c", "ok")
    assert kept["text"].startswith("I can't guarantee approval.")


def test_underwriting_disclosure_precedes_first_price_only(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Great.", "intent": "provide_info"},
                           {"reply": "Essential Care starts from $189 per month.", "intent": "ask_question", "citations": [0]},
                           {"reply": "Essential Care starts from $189 per month.", "intent": "ask_question", "citations": [0]}])
    agent = make(kb, tmp_path, brain)
    first = run(agent, ["yes", "How much does Essential Care cost?"])[-1]
    assert first["text"].startswith("Just so you know, final coverage and premiums are subject to medical underwriting")
    assert "docs/underwriting_policy_v2.txt#mandatory-disclosures" in first["citations"]
    second = agent.turn("c", "How much does Essential Care cost?")
    assert not second["text"].startswith("Just so you know")


def test_llm_failure_degrades_to_rules(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"}, RuntimeError("provider down")])
    out = run(make(kb, tmp_path, brain), ["yes", "I'm 40"])[-1]
    assert out["lead"]["age"] == 40 and out["guard_events"][-1]["guard"] == "llm_error"


def test_fields_without_evidence_in_caller_words_are_rejected(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"},
                           {"reply": "Thanks.", "extracted": {"age": 29, "state": "AZ",
                                                              "medical_conditions": "declined to share",
                                                              "current_insurance": "none"}}])
    out = run(make(kb, tmp_path, brain), ["yes", "I am 29 and I live in Arizona"])[-1]
    assert out["lead"] == {"age": 29, "state": "AZ"}


def test_eligibility_claims_are_softened(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"},
                           {"reply": "Great, you're eligible for Essential Care and Family Shield."}])
    out = run(make(kb, tmp_path, brain), ["yes", "I'm 29"])[-1]
    assert "eligible for essential" not in out["text"].lower() and "decided by medical underwriting" in out["text"]


def test_fallback_understands_corrections_and_word_numbers(kb, tmp_path):
    agent = make(kb, tmp_path)
    outs = run(agent, ["yes", "I'm 38", "Ohio", "the two of us", "Sorry, I'm actually 48"])
    assert outs[3]["lead"]["household_size"] == 2
    assert outs[-1]["conflicts"] == {"age": {"previous": 38, "new": 48}}


def test_objection_question_is_not_stacked_with_qualification_question(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure.", "intent": "provide_info"},
                           {"reply": "I hear you. What monthly budget do you have in mind?",
                            "intent": "objection", "citations": [0]}])
    out = run(make(kb, tmp_path, brain), ["yes", "This is too expensive"])[-1]
    assert out["text"].count("?") == 1 and "budget" in out["text"]


# --- discovery routing: the caller decides what the call is about

def test_after_consent_agent_asks_how_it_can_help_not_for_age(kb, tmp_path):
    out = run(make(kb, tmp_path), ["Yes, that's okay."])[-1]
    assert "how can i help" in out["text"].lower() and "how old" not in out["text"].lower()


def test_question_first_then_caller_opts_in_to_quote(kb, tmp_path):
    agent = make(kb, tmp_path)
    answered = run(agent, ["yes", "Can I cancel my policy if I change my mind?"])[-1]
    assert answered["citations"] == ["web/faq.html#can-i-cancel-my-policy"]
    assert "check which plans might fit" in answered["text"] and "how old" not in answered["text"].lower()
    started = agent.turn("c", "Sure, let's do it")
    assert "few quick questions" in started["text"] and "how old" in started["text"].lower()


def test_existing_member_claim_goes_to_human(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "My claim was denied last week and I want to know why"])[-1]
    assert out["escalated"] and "existing policy" in out["text"]
    assert out["outcome"]["reason"].startswith("Existing member")


def test_existing_member_intent_needs_a_stated_request(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "I want to cancel my policy"])[-1]
    assert out["escalated"] and out["outcome"]["reason"].startswith("Existing member")


def test_complaint_goes_to_human(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "I want to make a complaint about your sales calls"])[-1]
    assert out["escalated"] and out["outcome"]["reason"] == "Caller raised a complaint"


def test_not_interested_ends_politely(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "I'm not interested, thanks"])[-1]
    assert out["end_call"] and out["outcome"]["status"] == "not_interested"


def test_caller_can_stop_the_questions(kb, tmp_path):
    agent = make(kb, tmp_path)
    run(agent, ["yes", "I need a quote", "I'm 40"])
    out = agent.turn("c", "Please stop asking questions, just tell me about the plans")
    assert agent.sessions["c"].mode == "discovery" and "which state" not in out["text"].lower()


def test_location_outside_us_is_handled_with_citation(kb, tmp_path):
    brain = ScriptedBrain([{"reply": ""}, {"reply": "Got it."}, {"reply": "Thanks.", "extracted": {"state": "New Delhi"}},
                           {"reply": "Thanks.", "extracted": {"state": "New Delhi"}}])
    agent = make(kb, tmp_path, brain)
    run(agent, ["Yes, I was okay.", "I am 24 years old", "New Delhi"])  # confirmed once before concluding
    out = agent.turn("c", "New Delhi")
    assert out["outcome"]["status"] == "not_eligible" and "outside the United States" in out["text"]
    assert "docs/underwriting_policy_v2.txt#eligibility-rules" in out["citations"]


def test_not_eligible_reply_cites_rule(kb, tmp_path):
    out = run(make(kb, tmp_path), ["yes", "I'm 30", "I live in New York"])[-1]
    assert out["outcome"]["status"] == "not_eligible"
    assert out["citations"] == ["docs/underwriting_policy_v2.txt#eligibility-rules"]


# --- regressions from the recorded call 2128835c (2026-10-02 13:28)

def test_plain_yes_is_captured_when_the_model_extracts_nothing(kb, tmp_path):
    """The model returned consent_to_contact: null for "yes", which looped the question forever."""
    brain = ScriptedBrain([{"reply": "Sure."}] + [{"reply": "Thanks.", "extracted": {}}] * 9)
    agent = make(kb, tmp_path, brain)
    outs = run(agent, ["yes", "I need a plan", "I'm 38", "Ohio", "Four of us", "Yes.", "No conditions",
                       "Yes.", "Today after this call", "yes"])
    assert outs[-1]["lead"]["consent_to_contact"] is True
    assert outs[-1]["outcome"]["status"] == "qualified"


def test_disclosed_condition_is_never_recorded_as_none(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."}, {"reply": "Ok."}, {"reply": "Ok."},
                           {"reply": "Noted.", "extracted": {"medical_conditions": "none"}}])
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I need a plan", "I'm 38", "Ohio"])
    agent.sessions["c"].lead.update({"household_size": 4, "tobacco_use": True})
    out = agent.turn("c", "Yes, I have a little heart condition.")
    assert out["lead"]["medical_conditions"] == "Yes, I have a little heart condition."
    assert out["guard_events"][-1]["guard"] == "caller_words_override"


def test_self_disclosed_tobacco_use_is_captured(kb, tmp_path):
    assert regex_extract("Only I do tobacco. None of my family members.", "tobacco_use")["tobacco_use"] is True
    assert regex_extract("No, neither of us smokes.", "tobacco_use")["tobacco_use"] is False
    assert regex_extract("No, we don't have a medical condition.", "medical_conditions")["medical_conditions"] == "none"


def test_misplaced_unavailable_reply_is_dropped(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."}, {"reply": "Ok."},
                           {"reply": "I'm sorry, I don't have verified information on that."}])
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I need a plan", "I'm 38"])
    out = agent.turn("c", "Ohio")
    assert "verified information" not in out["text"]


def test_identical_reply_is_not_repeated(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."},
                           {"reply": "Thank you for confirming your consent."},
                           {"reply": "Thank you for confirming your consent."}])
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I need a plan", "I'm 38"])
    out = agent.turn("c", "Ohio")
    assert out["text"].count("Thank you for confirming your consent") == 0
    assert agent.sessions["c"].guard_events[-1]["guard"] == "repeated_reply"


def test_unanswerable_optional_field_is_abandoned_not_looped(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."}, {"reply": "Ok."}, {"reply": "Ok."}]
                          + [{"reply": "Sorry?", "extracted": {}}] * 4)
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I need a plan", "I'm 38", "Ohio"])
    for _ in range(3):
        agent.turn("c", "hmm")
    assert agent.sessions["c"].lead["household_size"] == "not provided"
    assert any(g["guard"] == "field_abandoned" for g in agent.sessions["c"].guard_events)


def test_missing_required_field_escalates_rather_than_looping(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."}] + [{"reply": "Sorry?", "extracted": {}}] * 4)
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I need a plan"])
    for _ in range(3):
        out = agent.turn("c", "mmm")
    assert out["escalated"] and "age" in out["outcome"]["reason"]


def test_prose_reply_from_a_json_failure_is_recovered(kb, tmp_path):
    """gpt-oss sometimes answers in plain text; the sentence is kept instead of losing the turn."""
    from groq import BadRequestError

    from app.llm import failed_generation
    exc = BadRequestError.__new__(BadRequestError)
    exc.body = {"error": {"code": "json_validate_failed", "failed_generation": "Sure, I can help with that."}}
    assert failed_generation(exc) == "Sure, I can help with that."
    exc.body = {"error": {"code": "other"}}
    assert failed_generation(exc) is None


# --- regressions from the recorded demo calls (Evidence/, 2026-10-02)

def test_transcription_debris_is_not_treated_as_a_country(kb, tmp_path):
    """Whisper heard "I.O" for "Ohio"; that ended the call as not eligible."""
    assert "state" not in regex_extract("I.O.", "state")
    assert "state" not in regex_extract("uh", "state")
    assert regex_extract("New Delhi", "state")["state"] == "New Delhi"


def test_rule_based_turn_still_captures_a_non_us_location(kb, tmp_path):
    assert regex_extract("I live in New Delhi", "state")["state"] == "New Delhi"
    assert regex_extract("Ohio", "state")["state"] == "OH"
    assert "state" not in regex_extract("I don't know", "state")


def test_non_us_location_is_confirmed_before_ending_the_call(kb, tmp_path):
    agent = make(kb, tmp_path)
    outs = run(agent, ["yes", "I want a quote", "I'm 24", "I live in New Delhi"])
    assert outs[-1]["outcome"]["status"] == "in_progress"
    assert "make sure I heard that right" in outs[-1]["text"]
    confirmed = agent.turn("c", "New Delhi")
    assert confirmed["outcome"]["status"] == "not_eligible" and confirmed["end_call"]


def test_confirmation_turn_concludes_and_never_escalates(kb, tmp_path):
    agent = make(kb, tmp_path)
    run(agent, ["yes", "I want a quote", "I'm 24", "I live in New Delhi"])
    out = agent.turn("c", "Yes, New Delhi, India.")
    assert out["outcome"]["status"] == "not_eligible" and not out["escalated"]


def test_confirmation_turn_accepts_a_corrected_us_state(kb, tmp_path):
    agent = make(kb, tmp_path)
    run(agent, ["yes", "I want a quote", "I'm 24", "I live in New Delhi"])
    out = agent.turn("c", "Sorry, I meant Ohio.")
    assert out["lead"]["state"] == "OH" and out["outcome"]["status"] == "in_progress"


def test_natural_agreement_counts_as_consent(kb, tmp_path):
    for phrase in ["That's fine.", "Sounds good", "Absolutely", "Please do"]:
        assert regex_extract(phrase, "consent_to_contact")["consent_to_contact"] is True
    assert regex_extract("Rather not", "consent_to_contact")["consent_to_contact"] is False


# --- regressions from recorded call 4 (Evidence/call4.json)

def test_number_the_caller_said_in_words_is_not_treated_as_invented(kb, tmp_path):
    from app.agent import spoken_numbers
    assert spoken_numbers("Ten Thousand") == {"10000"}
    assert spoken_numbers("about five hundred a month") == {"500"}
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."},
                           {"reply": "I understand a $10,000 monthly budget is a concern."}])
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I want a quote"])
    out = agent.turn("c", "Ten Thousand")
    assert "$10,000" in out["text"]


def test_objection_is_not_counted_as_a_failure_to_answer(kb, tmp_path):
    """Call 4 escalated with "could not capture consent" after two objections."""
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."}]
                          + [{"reply": "I hear you.", "intent": "objection", "citations": []}] * 4)
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I want a quote"])
    for _ in range(3):
        out = agent.turn("c", "This is too expensive for me")
    assert not out["escalated"] and agent.sessions["c"].attempts.get("age", 0) < 3


def test_objection_text_is_never_stored_as_a_free_text_answer(kb, tmp_path):
    brain = ScriptedBrain([{"reply": "Sure."}, {"reply": "Ok."},
                           {"reply": "I hear you.", "intent": "objection",
                            "extracted": {"callback_time": "Honestly, this sounds too expensive for me."}}])
    agent = make(kb, tmp_path, brain)
    run(agent, ["yes", "I want a quote"])
    out = agent.turn("c", "Honestly, this sounds too expensive for me.")
    assert "callback_time" not in out["lead"]


def test_api_call_flow():
    client = TestClient(app)
    start = client.post("/calls/start").json()
    call_id = start["call_id"]
    client.post("/agent/turn", json={"call_id": call_id, "customer_text": "yes"})
    turn = client.post("/agent/turn", json={"call_id": call_id, "customer_text": "I'm 30 and I live in Florida"}).json()
    assert turn["lead"] == {"age": 30, "state": "FL"}
    assert client.get(f"/calls/{call_id}").json()["lead"]["state"] == "FL"
    assert client.post(f"/calls/{call_id}/end").json()["outcome"]["status"] == "in_progress"
    assert client.get("/crm/leads").status_code == 200
