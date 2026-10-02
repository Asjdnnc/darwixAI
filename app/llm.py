"""Groq adapter: the conversational 'brain' of the Q1 agent, plus the Q4 baseline signal rules."""
from __future__ import annotations

import json

from groq import BadRequestError, Groq

from app.config import settings
from app.models import Chunk, Signal

LANGUAGES = {"en": "English", "ph": "natural Taglish (Filipino/English)", "id": "Bahasa Indonesia"}

SYSTEM_PROMPT = """You are Austin, a virtual health insurance advisor for Darwix Health, on a recorded phone call \
for new health insurance customers. You first find out why the caller is calling and help them: answer \
questions, handle concerns, and, when they want it, check which plans might fit by qualifying them for a quote. \
You are an AI and say so if asked.

GROUNDING
- Facts about plans, prices, benefits, eligibility, waiting periods, claims, policies and procedures may ONLY come \
from the numbered EXCERPTS. Put the index of every excerpt you used in "citations".
- If the caller asks something the excerpts do not answer, say you don't have verified information on that and \
that a licensed advisor can help. Never guess or use outside knowledge. Set "answered_from_excerpts" to false.
- For objections, follow the approved guidance in the excerpts: acknowledge, respond, never pressure.
- Never tell the caller they are eligible, qualified or approved (the system gives the preliminary outcome). \
Never guarantee approval or coverage, never quote a final premium, never give medical advice, never criticise \
other insurers.

CONVERSATION
- Spoken style: at most 2 short sentences and 45 words, no lists or markdown, numbers as digits.
- Your reply is only the first part of the turn: briefly acknowledge what the caller said, and answer their \
question or handle their objection if they raised one. Do NOT ask qualification questions and do NOT end with a \
question: the system appends the next qualification question (or a read-back of conflicting answers) after your reply.
- If the caller discloses a medical condition, acknowledge it briefly and say a licensed advisor will review \
it during underwriting. Never tell callers you lack information about something they just told you.
- If OUTCOME status is not_eligible, acknowledge kindly; the system explains the reason and closes.
- If the caller says goodbye or has nothing else after qualification, close politely (caller_intent "end_call").
- MODE "discovery": the caller has not asked for a quote yet; help with whatever they need and never start \
asking qualification questions yourself. MODE "qualifying": the caller wants a quote.
- caller_intent describes what the CALLER did in their latest message, never what you are doing:
  provide_info = answering a question or giving details; ask_question = asking about plans, policies or process;
  wants_quote = wants coverage, a quote, or to find a plan; objection = a concern or pushback;
  out_of_scope = asks about something Darwix Health does not offer; human_request = explicitly asks for a person;
  existing_member = about their own existing policy, claim, bill or membership; complaint = complains;
  not_interested = does not want coverage; decline_questions = does not want to answer more questions;
  small_talk; end_call = goodbye.

EXTRACTION
- Extract only what the caller explicitly said in their LATEST message; otherwise null. Never infer.
- age: integer. state: two-letter US code, or the place name as said if it is outside the US. household_size: integer people needing coverage (including caller). \
tobacco_use / consent_to_contact: true or false. medical_conditions: short text, "none", or "declined to share". \
current_insurance: short text or "none". callback_time: the caller's words.
- A bare "yes"/"no" or a short answer responds to QUESTION_JUST_ASKED.
- recording_consent: only when the caller is answering the recording question: true, false, otherwise null.

Return ONLY JSON:
{"reply": str, "citations": [int], "caller_intent": "provide_info|ask_question|wants_quote|objection|out_of_scope|\
human_request|existing_member|complaint|not_interested|decline_questions|small_talk|end_call", "answered_from_excerpts": bool, "recording_consent": true|false|null,
 "extracted": {"age": null, "state": null, "household_size": null, "tobacco_use": null, "medical_conditions": null,
  "current_insurance": null, "callback_time": null, "consent_to_contact": null}}"""


def failed_generation(exc: BadRequestError) -> str | None:
    """The text a JSON-mode rejection ('json_validate_failed') was carrying, if any."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    text = (error or {}).get("failed_generation") if isinstance(error, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else None


REMINDER_PROMPT = """You are {agent_name}, a virtual servicing assistant for {company}, a {sector} \
company. You are on a recorded outbound {flow} call. You are an AI and say so if asked.

LANGUAGE — THIS IS THE MOST IMPORTANT RULE
- Reply ONLY in {language}
- Never switch to English sentences. English words appear ONLY as the finance/banking loanwords that \
customers themselves use. A whole sentence in English is wrong, even when apologising, even when you \
do not know something, even when escalating.
- Match the caller's level of formality.

GROUNDING
- Facts about amounts, dates, penalties, policies, channels and procedures may come ONLY from the \
numbered EXCERPTS. Put the index of every excerpt you used in "citations".
- If the excerpts do not answer the question, say so in {language} and offer a human agent. Never guess.
- NEVER state a specific amount owed, penalty or settlement figure: those are computed by the system.
- Never threaten, never promise that an application or concession will be approved.

CONVERSATION
- Spoken style: at most 2 short sentences, under 40 words. No lists, no markdown.
- Acknowledge what the caller said and answer their question. Do NOT ask a question and do NOT end \
with a question: the system appends the next question itself.

Return ONLY JSON:
{{"reply": str, "citations": [int], "caller_intent": "provide_info|ask_question|objection|out_of_scope|\
human_request|already_paid|hardship|dispute|refuse|promise|small_talk|end_call", "answered_from_excerpts": bool}}"""

AGENTS = {"ph": ("Maya", "Darwix Life Philippines"), "id": ("Rani", "Darwix Finance Indonesia")}


class GroqService:
    def converse_reminder(self, session, text: str, context, expected, flw) -> dict:
        """Q3 turn: the market flow supplies the language, sector and register."""
        if not settings.groq_api_key:
            return {"reply": "", "citations": [], "intent": "provide_info", "extracted": {}}
        agent_name, company = AGENTS[flw.market]
        system = REMINDER_PROMPT.format(agent_name=agent_name, company=company, sector=flw.sector,
                                        flow=flw.name.replace("_", " "), language=flw.language)
        excerpts = [{"index": i, "title": c.title, "text": c.content} for i, c in enumerate(context)]
        history = [{"role": "assistant" if t["role"] == "agent" else "user", "content": t["text"]}
                   for t in session.transcript[-8:-1]]
        user = (f"EXCERPTS: {json.dumps(excerpts, ensure_ascii=False) if excerpts else '[] (none retrieved)'}\n"
                f"ALREADY CAPTURED: {json.dumps(session.lead, ensure_ascii=False)}\n"
                f"LATEST CALLER MESSAGE: {text}\n\nRespond with the JSON object only, in {flw.language}.")
        reasoning = {"reasoning_effort": "low"} if "gpt-oss" in settings.groq_model else {}
        raw = None
        for attempt in range(2):
            try:
                response = Groq(api_key=settings.groq_api_key, timeout=20, max_retries=4).chat.completions.create(
                    model=settings.groq_model, temperature=0.2 if attempt == 0 else 0.0,
                    response_format={"type": "json_object"}, max_completion_tokens=1200, **reasoning,
                    messages=[{"role": "system", "content": system}, *history, {"role": "user", "content": user}])
                raw = response.choices[0].message.content or "{}"
                break
            except BadRequestError as exc:
                prose = failed_generation(exc)
                if prose:
                    raw = prose
                    break
                if attempt == 1:
                    raise
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            data = {"reply": raw.strip(), "citations": [], "recovered_prose": True}
        data["intent"] = data.pop("caller_intent", None) or "provide_info"
        data["citations"] = [i for i in data.get("citations", []) if isinstance(i, int)]
        data.setdefault("reply", "")
        return data

    def converse(self, session, text: str, context: list[Chunk], expected: str | None, rules: dict) -> dict:
        """One structured LLM call per turn. Raises on provider errors; the agent falls back to rules."""
        if not settings.groq_api_key:
            from app.agent import fallback_brain
            return fallback_brain(session, text, context, expected)
        state = {
            "LANGUAGE": LANGUAGES.get(session.market, "English"),
            "MODE": session.mode,
            "LEAD_SO_FAR": session.lead,
            "CONFLICTS": session.conflicts,
            "QUESTION_JUST_ASKED": next((t["text"] for t in reversed(session.transcript[:-1]) if t["role"] == "agent"), None),
            "FIELD_EXPECTED": expected,
            "OUTCOME": session.outcome,
            "UNDERWRITING_DISCLOSURE_GIVEN": session.underwriting_disclosed,
            "AWAITING_RECORDING_CONSENT": session.recording_consent is None,
        }
        excerpts = [{"index": i, "title": c.title, "category": c.category, "text": c.content} for i, c in enumerate(context)]
        history = [{"role": "assistant" if t["role"] == "agent" else "user", "content": t["text"]}
                   for t in session.transcript[-12:-1]]
        user = (f"STATE: {json.dumps(state)}\nEXCERPTS: {json.dumps(excerpts) if excerpts else '[] (none retrieved)'}\n"
                f"LATEST CALLER MESSAGE: {text}\n\nRespond with the JSON object only.")
        client = Groq(api_key=settings.groq_api_key, timeout=20, max_retries=4)
        # gpt-oss models spend tokens on hidden reasoning; "low" keeps latency and daily quota down.
        reasoning = {"reasoning_effort": "low"} if "gpt-oss" in settings.groq_model else {}
        raw = None
        for attempt in range(2):  # JSON mode occasionally rejects a generation; retry once colder
            try:
                response = client.chat.completions.create(
                    model=settings.groq_model, temperature=0.2 if attempt == 0 else 0.0,
                    response_format={"type": "json_object"}, max_completion_tokens=1400, **reasoning,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user}],
                )
                raw = response.choices[0].message.content or "{}"
                break
            except BadRequestError as exc:
                # The model sometimes answers in prose. The sentence it wrote is still usable, so
                # keep it rather than discarding the whole turn; rules then fill in the fields.
                prose = failed_generation(exc)
                if prose:
                    raw = prose
                    break
                if attempt == 1:
                    raise
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError:
            from app.agent import fallback_brain
            data = {**fallback_brain(session, text, context, expected), "reply": raw.strip(),
                    "recovered_prose": True}
        data.setdefault("reply", "")
        data["intent"] = data.pop("caller_intent", None) or data.get("intent") or "provide_info"
        data["citations"] = [i for i in data.get("citations", []) if isinstance(i, int)]
        data["extracted"] = data.get("extracted") or {}
        return data

    def classify_signals(self, system_prompt: str, chunk: str) -> list[dict]:
        """Q4 second-pass classifier. Raises on provider errors; the caller falls back to rules."""
        if not settings.groq_api_key:
            return []
        reasoning = {"reasoning_effort": "low"} if "gpt-oss" in settings.groq_model else {}
        response = Groq(api_key=settings.groq_api_key, timeout=8, max_retries=0).chat.completions.create(
            model=settings.groq_model, temperature=0, response_format={"type": "json_object"},
            max_completion_tokens=400, **reasoning,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": chunk}])
        data = json.loads(response.choices[0].message.content or "{}")
        return data.get("signals", []) if isinstance(data, dict) else []

    def detect_signals(self, event_text: str) -> list[Signal]:
        text = event_text.lower()
        # Deterministic rules are the resilient baseline and test oracle; Groq can be
        # added here as a second-pass classifier once real call data is available.
        rules = [
            ("frustration", ["angry", "frustrated", "again", "terrible", "transfer me to a human"], "Customer shows frustration."),
            ("payment_difficulty", ["cannot pay", "can't pay", "financial difficulty", "too expensive"], "Customer reports payment difficulty."),
            ("callback", ["call me back", "callback", "later"], "Customer requests a callback."),
            ("cross_sell", ["wife", "husband", "spouse", "children", "kids", "family"], "Customer mentioned family members (Family Plan Cross-sell)."),
            ("compliance_gap", ["no disclosure", "not disclosed"], "Potential required disclosure omission."),
        ]
        return [Signal(topic=topic, confidence=0.86, evidence=evidence) for topic, terms, evidence in rules if any(term in text for term in terms)]
