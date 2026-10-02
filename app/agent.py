"""Q1 knowledge-grounded voice agent for health-insurance lead qualification.

One customer turn runs this pipeline:

    customer text
      -> deterministic intent checks (human request, recording refusal)
      -> grounded retrieval from the Q2 knowledge base (score + coverage gates)
      -> brain: Groq LLM returns {reply, citations, intent, extracted fields}
                 (rule-based fallback brain if Groq is unavailable)
      -> merge fields; detect conflicting answers
      -> evaluate qualification rules in code (data/rules/qualification_rules.json)
      -> business actions: CRM lead, callback, escalation
      -> guards on the reply: unsupported question, invented numbers, prohibited promises,
         underwriting disclosure before prices
      -> reply + citations + lead state

The LLM writes natural language; code owns eligibility, compliance, and escalation.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.crm import MockCRM, now_iso
from app.kb import KnowledgeBase
from app.models import Chunk

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "data" / "rules" / "qualification_rules.json").read_text())
CALL_LOG_DIR = ROOT / "test_logs" / "calls"

DISCLOSURE_REF = "docs/underwriting_policy_v2.txt#mandatory-disclosures"
ESCALATION_REF = "docs/underwriting_policy_v2.txt#human-escalation"
PROHIBITED_REF = "docs/underwriting_policy_v2.txt#prohibited-statements"

AGENT_NAME = "Austin"
GREETING = (f"Hi, this is {AGENT_NAME}, a virtual health insurance advisor with Darwix Health. "
            "This call is recorded for quality and compliance purposes. Is it okay if we continue?")
UNDERWRITING_DISCLOSURE = ("Just so you know, final coverage and premiums are subject to medical underwriting "
                           "and depend on verified medical history.")
ESCALATION_REPLY = ("Of course. I'll connect you with a licensed advisor. Our advisors are available Monday to Friday, "
                    "8 AM to 8 PM Central, and Saturday 9 AM to 2 PM Central. If no one is available right now, "
                    "we'll call you back within 1 business day.")
DECLINED_RECORDING_REPLY = ("No problem, I understand. I'll end this automated call now and arrange for a human "
                            "advisor to call you back instead. Thank you for your time.")
DISCOVERY_PROMPT = ("How can I help you today? I can answer questions about our health plans, "
                    "or check which plan might fit you.")
OFFER_PROMPT = "Is there anything else I can help with, or would you like me to check which plans might fit you?"
START_QUALIFYING = "Happy to help with that. I'll ask a few quick questions to see which plans might fit."
EXISTING_MEMBER_REPLY = ("This line is for new coverage, so I'll connect you with a licensed advisor who can help with "
                         "your existing policy. Our advisors are available Monday to Friday, 8 AM to 8 PM Central, and "
                         "Saturday 9 AM to 2 PM Central, or we'll call you back within 1 business day.")
COMPLAINT_REPLY = ("I'm sorry about your experience. I'll pass this to a licensed advisor so it's handled properly. "
                   "If no one is available right now, we'll call you back within 1 business day.")
NOT_INTERESTED_REPLY = ("No problem at all. If you ever have questions about health coverage, we're happy to help. "
                        "Thank you for calling Darwix Health.")
UNAVAILABLE = {
    "en": "I'm sorry, I don't have verified information about that, so I won't guess.",
    "ph": "Pasensya na, wala akong verified na impormasyon tungkol diyan, kaya hindi ako manghuhula.",
    "id": "Mohon maaf, saya belum punya informasi yang terverifikasi soal itu, jadi saya tidak mau menebak.",
}
OFFER_HUMAN = {
    "en": "A licensed advisor can help with that question.",
    "ph": "Matutulungan ka ng isang licensed advisor diyan.",
    "id": "Petugas berlisensi kami bisa membantu pertanyaan itu.",
}

HUMAN_RE = re.compile(r"\b(real person|human|live agent|an agent|representative|speak (?:to|with) (?:someone|a person|an advisor)|"
                      r"talk (?:to|with) (?:someone|a person|an advisor)|supervisor|manager)\b", re.I)
DECLINE_RECORDING_RE = re.compile(r"(don'?t|do not|not okay|not ok|no,? (?:please )?don'?t) (?:want to be |want you to )?record|"
                                  r"no recording|stop recording|not comfortable (?:with|being) record", re.I)
PERSON_RE = re.compile(r"\b(person|someone|somebody|human|agent|advisor|representative|people|supervisor|manager)\b", re.I)
# Discovery routing: why is the caller here?
QUOTE_RE = re.compile(r"\b(quote|looking for (?:a |some )?(?:health )?(?:insurance|coverage|plan)|need (?:a |some )?(?:health )?"
                      r"(?:insurance|coverage|plan)|want (?:a |some )?(?:health )?(?:insurance|coverage|plan)|sign up|enrol+|"
                      r"(?:insurance|coverage|plan) for (?:me|my|us|our)|which plan (?:is|would) (?:best|right)|get covered)\b", re.I)
EXISTING_RE = re.compile(r"\b(my (?:claim|bill|member ?(?:id|card)|(?:existing|current) (?:policy|plan) with you)|i'?m (?:already )?a member|"
                         r"existing (?:policy|member|customer)|claim (?:was |got )?(?:denied|rejected)|"
                         # "Can I cancel my policy if...?" is a prospect's question; only a stated intent counts.
                         r"(?:want|need|like|trying) to (?:cancel|change|renew|update) my (?:policy|plan|address|details))\b", re.I)
COMPLAINT_RE = re.compile(r"\b(complain|complaint|unacceptable|ripped off|report you|very disappointed)\b", re.I)
NOT_INTERESTED_RE = re.compile(r"\b(not interested|just (?:browsing|looking around)|stop calling|"
                               r"don'?t (?:want|need) (?:any |a )?(?:insurance|quote|plan))\b", re.I)
DECLINE_QUESTIONS_RE = re.compile(r"\b(stop asking|no more questions|don'?t want to answer (?:any|these|more)|"
                                  r"just (?:answer|tell me)|why so many questions)\b", re.I)
YES_RE = re.compile(r"^\W*(yes|yeah|yep|sure|ok|okay|please|go ahead|sounds good|let'?s do it|why not)\b", re.I)
QUESTION_RE = re.compile(r"\?\s*$|^(what|how|when|where|which|who|why|can|could|do|does|is|are|will|would|should)\b", re.I)
GUARANTEE_RE = re.compile(r"[^.!?]*\b(guarantee[sd]?|you(?:'re| are| will be) (?:definitely )?(?:approved|covered|eligible|qualified)|"
                          r"you qualify)\b[^.!?]*[.!?]?", re.I)
NEGATED_RE = re.compile(r"\b(?:can'?t|cannot|can not|don'?t|do not|won'?t|unable to|not able to|no one can)\s+(?:\w+\s+){0,2}"
                        r"guarantee|\bno guarantee|\bnot (?:be )?(?:guaranteed|automatically approved)", re.I)
NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
WORD_NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
WORD_NUMBERS.update({"just me": 1, "only me": 1, "myself": 1, "both of us": 2, "the two of us": 2, "couple": 2})
STATE_NAMES = {code: name.title() for name, code in US_STATES.items()}


# --------------------------------------------------------------------------- state

@dataclass
class CallSession:
    call_id: str
    market: str = "en"
    started_at: str = field(default_factory=now_iso)
    transcript: list[dict] = field(default_factory=list)
    lead: dict = field(default_factory=dict)
    conflicts: dict = field(default_factory=dict)  # field -> {"previous": x, "new": y}
    recording_consent: bool | None = None
    underwriting_disclosed: bool = False
    outcome: dict = field(default_factory=lambda: {"status": "in_progress"})
    actions: list[dict] = field(default_factory=list)
    guard_events: list[dict] = field(default_factory=list)
    escalated: bool = False
    ended: bool = False
    attempts: dict = field(default_factory=dict)  # field -> times asked without a usable answer
    last_asked: str | None = None
    mode: str = "discovery"  # discovery: helping/answering; qualifying: collecting lead details
    quote_offered: bool = False
    pending_non_us: str | None = None  # place heard once, awaiting confirmation before ending the call
    qualifying_started_turn: bool = False

    def add(self, role: str, text: str, **extra) -> None:
        self.transcript.append({"at": now_iso(), "role": role, "text": text, **extra})

    def snapshot(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- field handling

def normalize_field(name: str, value):
    """Validate and normalize one extracted field; returns None when the value is unusable."""
    if value is None or value == "":
        return None
    if name in {"age", "household_size"}:
        text = str(value).lower()
        match = re.search(r"\d+", text)
        word = next((n for w, n in WORD_NUMBERS.items() if re.search(rf"\b{w}\b", text)), None)
        number = int(match.group()) if match else word
        limits = (0, 120) if name == "age" else (1, 12)
        return number if number is not None and limits[0] <= number <= limits[1] else None
    if name == "state":
        text = str(value).strip()
        if text.upper() in STATE_NAMES:
            return text.upper()
        code = US_STATES.get(text.lower().replace(".", ""))
        if code:
            return code
        # A real place outside the US (e.g. "New Delhi") is an answer, not a parsing failure.
        return f"{text.title()} (outside the US)" if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,40}", text) else None
    if name in {"tobacco_use", "consent_to_contact"}:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"yes", "true", "y"}:
            return True
        if text in {"no", "false", "n"}:
            return False
        return None
    return str(value).strip()[:200]


ANSWERING = {"provide_info", "small_talk"}  # intents where the caller is answering our question
FREE_TEXT = {"callback_time", "current_insurance", "medical_conditions"}
EVIDENCE = {
    "age": r"\d|\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty)\b",
    "household_size": r"\d|\b(?:me|myself|us|wife|husband|kids?|children|family|one|two|three|four|five|six|seven|eight)\b",
    "tobacco_use": r"smok|tobacco|vape|cigar|nicotine",
    "medical_conditions": r"condition|diabet|asthma|cancer|heart|blood pressure|transplant|dialysis|hospital|healthy|surgery|illness",
    "current_insurance": r"insur|coverage|covered|plan|medicaid|medicare|employer|job",
    "callback_time": r"morning|afternoon|evening|night|weekday|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d|am\b|pm\b|tomorrow|today",
}


def has_evidence(name: str, value, text: str, expected: str | None) -> bool:
    """Accept an extracted field only if it answers the question just asked or the caller's words support it."""
    if name == expected:
        return True
    if name == "state":
        code = normalize_field("state", value)
        if code and code.endswith("(outside the US)"):
            return code.replace(" (outside the US)", "").lower() in text.lower()
        return bool(code) and (re.search(rf"\b{re.escape(STATE_NAMES.get(code, '').lower())}\b", text.lower()) is not None
                               or re.search(rf"\b{code}\b", text) is not None)
    pattern = EVIDENCE.get(name)
    return bool(pattern and re.search(pattern, text, re.I))


def reconcile(extracted: dict, text: str, expected: str | None, intent: str = "provide_info") -> tuple[dict, bool]:
    """Let the caller's literal words settle the field that was just asked.

    The model sometimes returns null for a plain "yes", or summarizes a disclosed condition as
    "none". Both would silently corrupt the lead or loop the question, so for `expected` a
    confident rule-based reading of the caller's words wins over the model's value.
    """
    if not expected or intent not in ANSWERING:
        return extracted, False
    literal = regex_extract(text, expected).get(expected)
    if literal is None:
        return extracted, False
    model_value = extracted.get(expected)
    overridden = model_value is not None and str(model_value).strip().lower() != str(literal).strip().lower()
    return {**extracted, expected: literal}, overridden


def merge_fields(session: CallSession, extracted: dict, text: str = "", expected: str | None = None,
                 intent: str = "provide_info") -> list[str]:
    """Merge newly extracted fields. A changed answer becomes a conflict to confirm, never a silent overwrite."""
    changed = []
    for name in RULES["collect_order"]:
        value = normalize_field(name, extracted.get(name))
        if value is None:
            continue
        if session.lead.get(name) == value and name not in session.conflicts:
            continue  # the model repeated a value we already hold
        if name in FREE_TEXT and intent not in ANSWERING:
            session.guard_events.append({"guard": "not_an_answer", "field": name, "intent": intent})
            continue
        if text and not has_evidence(name, value, text, expected):
            session.guard_events.append({"guard": "unsupported_extraction", "field": name, "value": value})
            continue
        if name in session.conflicts:
            session.lead[name] = value  # the caller has answered the read-back question
            del session.conflicts[name]
            changed.append(name)
        elif name in session.lead and session.lead[name] != value and name in {"age", "state", "household_size", "tobacco_use"}:
            session.conflicts[name] = {"previous": session.lead[name], "new": value}
        elif session.lead.get(name) != value:
            session.lead[name] = value
            changed.append(name)
    return changed


def next_field(session: CallSession) -> str | None:
    if session.mode != "qualifying":
        return None
    if session.outcome["status"] in {"not_eligible", "no_consent", "escalated", "recording_declined"}:
        return None
    return next((f for f in RULES["collect_order"] if f not in session.lead), None)


def eligible_plans(lead: dict) -> list[str]:
    age = lead.get("age")
    if not isinstance(age, int):
        return []  # unknown or unusable age: no plan can be suggested
    household = lead.get("household_size")
    household = household if isinstance(household, int) else 1
    return [p["name"] for p in RULES["plans"]
            if p["min_age"] <= age <= p["max_age"] and household >= p.get("min_household", 1)]


def evaluate_outcome(session: CallSession) -> dict:
    """Preliminary outcome from the written rules (final eligibility is underwriting's decision)."""
    lead = session.lead
    if session.escalated:
        return {**session.outcome, "status": "escalated"}
    if session.recording_consent is False:
        return {"status": "recording_declined", "reason": "Caller declined call recording", "source_ref": DISCLOSURE_REF}
    conditions = str(lead.get("medical_conditions", "")).lower()
    if any(term in conditions for term in RULES["referral_conditions"]):
        return {"status": "referred", "reason": "Reported condition requires a senior health advisor",
                "plans": eligible_plans(lead), "source_ref": RULES["referral_source_ref"]}
    if "state" in lead and lead["state"] not in RULES["offered_states"]:
        place = STATE_NAMES.get(lead["state"], lead["state"].replace(" (outside the US)", ""))
        reason = (f"Plans are only offered to residents of 12 US states, and {place} is outside the United States"
                  if lead["state"].endswith("(outside the US)") else f"Plans are not offered in {place}")
        return {"status": "not_eligible", "reason": reason, "source_ref": RULES["offered_states_source_ref"]}
    if "age" in lead and not eligible_plans({"age": lead["age"]}):
        return {"status": "not_eligible", "reason": f"No plan is available for age {lead['age']}",
                "source_ref": RULES["plans"][0]["source_ref"]}
    if lead.get("consent_to_contact") is False:
        return {"status": "no_consent", "reason": "Caller did not consent to be contacted"}
    if all(f in lead for f in RULES["required_for_outcome"]):
        complete = all(f in lead for f in RULES["collect_order"])
        return {"status": "qualified" if complete else "qualified_partial", "plans": eligible_plans(lead),
                "reason": "Age and state match at least one plan and the caller consented to contact",
                "source_ref": "docs/underwriting_policy_v2.txt#preliminary-lead-qualification"}
    return {"status": "in_progress", "plans": eligible_plans(lead)}


# --------------------------------------------------------------------------- fallback brain

def regex_extract(text: str, expected: str | None) -> dict:
    """Rule-based extraction used when the LLM is unavailable (and in deterministic tests)."""
    t = text.lower()
    out: dict = {}
    if m := re.search(r"\b(?:i'?m|i am|age(?: is)?|aged)\s+(?:actually\s+|now\s+|really\s+)?(\d{1,3})\b|\b(\d{1,3})\s*(?:years? old|yo)\b", t):
        out["age"] = m.group(1) or m.group(2)
    elif expected == "age" and (m := re.fullmatch(r"\D*(\d{1,3})\D*", t)):
        out["age"] = m.group(1)
    for name, code in sorted(US_STATES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{name}\b", t):
            out["state"] = code
            break
    if "state" not in out and expected == "state":
        # Rule-based turns must still capture a place the US-state table does not know ("New Delhi"),
        # otherwise an ineligible caller is asked the same question forever.
        match = re.search(r"\b(?:i (?:live|am|'m) in|i'm from|from|in)\b\s+(.+)$", text.strip(), re.I)
        candidate = re.sub(r"[.?!,]+$", "", (match.group(1) if match else text).strip())
        # Transcription debris ("I.O" for "Ohio") must never pass as a place: require real words.
        words = candidate.split()
        if (re.fullmatch(r"[A-Za-z][A-Za-z '-]{3,40}", candidate) and len(words) <= 3
                and all(len(w.strip("'-")) >= 3 for w in words)
                and not re.search(r"\b(know|sure|idea|what|why|how|yes|no|maybe|skip|here|there)\b", candidate, re.I)):
            out["state"] = candidate
    if m := re.search(r"\b(just me|only me|myself)\b", t):
        out["household_size"] = 1
    elif m := re.search(r"\b(?:family of|(\d+) (?:people|of us|persons))\s*(\d+)?", t):
        out["household_size"] = m.group(2) or m.group(1)
    elif expected == "household_size":
        out["household_size"] = t  # normalize_field understands digits, "four", "the two of us"
    # "Only I do tobacco. None of my family." -> True: a self-disclosure outweighs the negation about others.
    if re.search(r"\b(?:i|we)\b[^.!?]{0,20}\b(?:smoke|use tobacco|do tobacco|vape)\b|\bonly i\b|\bi'?m a smoker\b", t):
        out["tobacco_use"] = True
    elif re.search(r"\b(non-?smoker|no ?one|nobody|none of|never smoked)\b|\b(?:don'?t|doesn'?t|do not) smoke\b|\bno tobacco\b", t):
        out["tobacco_use"] = False
    elif re.search(r"\b(smoker|tobacco|vape|cigarettes?)\b", t):
        out["tobacco_use"] = True
    if expected == "medical_conditions":
        if re.search(r"\b(skip|rather not|prefer not|not comfortable)\b", t):
            out["medical_conditions"] = "declined to share"
        elif re.search(r"\b(no|none|nope|nothing|healthy)\b|\b(?:don'?t|doesn'?t|do not) have\b", t):
            out["medical_conditions"] = "none"
        else:
            # A disclosed condition is stored in the caller's own words, never summarized away.
            out["medical_conditions"] = text.strip()
    if expected == "current_insurance":
        out["current_insurance"] = "none" if re.search(r"\b(no|none|uninsured)\b", t) else text.strip()
    if expected == "callback_time":
        out["callback_time"] = text.strip()
    if expected in {"consent_to_contact", "tobacco_use"} and expected not in out:
        if re.search(r"\b(yes|yeah|yep|yup|sure|okay|ok|of course|go ahead|absolutely|correct|please do|"
                     r"that'?s fine|that works|sounds good|no problem|fine by me|certainly)\b", t):
            out[expected] = True
        elif re.search(r"\b(no|nope|nah|don'?t|do not|rather not|not really)\b", t):
            out[expected] = False
    return out


def fallback_brain(session: CallSession, text: str, context: list[Chunk], expected: str | None) -> dict:
    intent = "provide_info"
    reply = ""
    citations = []
    if HUMAN_RE.search(text):
        intent = "human_request"
    elif QUESTION_RE.search(text.strip()):
        intent = "ask_question"
        if context:
            sentences = re.split(r"(?<=[.!?])\s+", context[0].content)
            reply, citations = " ".join(sentences[:2]), [0]
    elif EXISTING_RE.search(text):
        intent = "existing_member"
    elif COMPLAINT_RE.search(text):
        intent = "complaint"
    elif NOT_INTERESTED_RE.search(text):
        intent = "not_interested"
    elif DECLINE_QUESTIONS_RE.search(text):
        intent = "decline_questions"
    elif QUOTE_RE.search(text) or (session.quote_offered and YES_RE.search(text)):
        intent = "wants_quote"
    elif re.search(r"\b(that'?s all|nothing else|goodbye|bye|no thanks?)\b", text, re.I) and expected is None:
        intent = "end_call"
        reply = "Thank you for calling Darwix Health. Have a great day!"
    elif context and context[0].category == "objection":
        intent, reply, citations = "objection", "I understand.", [0]
    recording = None
    if session.recording_consent is None:
        recording = False if DECLINE_RECORDING_RE.search(text) or re.fullmatch(r"\W*no\W*", text.lower()) else True
    return {"reply": reply, "citations": citations, "intent": intent, "recording_consent": recording,
            "extracted": regex_extract(text, expected), "answered_from_excerpts": bool(citations)}


# --------------------------------------------------------------------------- guards

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
NUMBER_WORDS.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
                     "eighty": 80, "ninety": 90})


def spoken_numbers(text: str) -> set[str]:
    """Digits for numbers said as words, so "ten thousand" is recognized as the caller's own figure."""
    found, current, total = set(), 0, 0
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in NUMBER_WORDS:
            current += NUMBER_WORDS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word in {"thousand", "k"}:
            total += max(current, 1) * 1000
            current = 0
        elif word == "million":
            total += max(current, 1) * 1000000
            current = 0
        elif total or current:
            found.add(str(total + current))
            total = current = 0
    if total or current:
        found.add(str(total + current))
    return found


def allowed_numbers(session: CallSession, context: list[Chunk]) -> set[str]:
    sources = [c.content for c in context] + [t["text"] for t in session.transcript if t["role"] == "customer"]
    sources += [json.dumps(session.lead), ESCALATION_REPLY, GREETING, " ".join(RULES["questions"].values())]
    spoken = {n for t in session.transcript if t["role"] == "customer" for n in spoken_numbers(t["text"])}
    return {n.strip("$%").replace(",", "") for s in sources for n in NUMBER_RE.findall(s)} | spoken


def guard_reply(session: CallSession, reply: str, context: list[Chunk], cited: list[Chunk],
                brain: dict, question: str | None) -> tuple[str, list[str]]:
    """Code-level safety net applied to every LLM reply. Returns (reply, extra citations)."""
    extra: list[str] = []
    unavailable = UNAVAILABLE.get(session.market, UNAVAILABLE["en"])

    # 1. A knowledge question with no retrieved evidence gets the safe answer, whatever the LLM wrote.
    #    Both the model and a surface check must agree it was a question, so answers are never mistaken for one.
    caller_text = session.transcript[-1]["text"] if session.transcript else ""
    if brain["intent"] in {"ask_question", "out_of_scope"} and QUESTION_RE.search(caller_text.strip()) and not context:
        session.guard_events.append({"guard": "unsupported_question", "original": reply})
        reply = f"{unavailable} {OFFER_HUMAN.get(session.market, OFFER_HUMAN['en'])}"

    # 1b. "I don't have verified information" belongs to questions, not to answers the caller gave.
    if not QUESTION_RE.search(caller_text.strip()) and re.search(r"don'?t have (?:verified|enough)|no verified information", reply, re.I):
        session.guard_events.append({"guard": "misplaced_unavailable", "original": reply})
        reply = "Thank you."

    # 1c. Repeating the previous reply word for word makes the agent sound broken.
    previous = next((t["text"] for t in reversed(session.transcript[:-1]) if t["role"] == "agent"), "")
    if reply.strip() and reply.strip() in previous:
        session.guard_events.append({"guard": "repeated_reply"})
        reply = ""

    # 1d. Playbook placeholders ("[grace end date]", "[amount]") must never reach the caller.
    if re.search(r"\[[^\]]{2,40}\]", reply):
        session.guard_events.append({"guard": "unfilled_placeholder", "original": reply})
        kept = [x for x in re.split(r"(?<=[.!?])\s+", reply) if not re.search(r"\[[^\]]{2,40}\]", x)]
        reply = " ".join(kept).strip()

    # 2. Every number in the reply must exist in the evidence, the conversation, or the lead record.
    invented = [n for n in NUMBER_RE.findall(reply) if n.strip("$%").replace(",", "") not in allowed_numbers(session, context)]
    if invented:
        session.guard_events.append({"guard": "ungrounded_number", "numbers": invented, "original": reply})
        asked = bool(QUESTION_RE.search(caller_text.strip()))
        reply = f"{unavailable} {OFFER_HUMAN.get(session.market, OFFER_HUMAN['en'])}" if asked else "Thank you."

    # 3. Prohibited promises (approval/coverage guarantees) are replaced with the compliant statement.
    def fix(match: re.Match) -> str:
        sentence = match.group(0)
        if NEGATED_RE.search(sentence):
            return sentence
        session.guard_events.append({"guard": "prohibited_guarantee", "original": sentence.strip()})
        extra.append(PROHIBITED_REF)
        if re.search(r"eligible|qualif", sentence, re.I):
            return " Final eligibility is decided by medical underwriting after a full application."
        return " I can't guarantee approval or coverage; final eligibility is decided by medical underwriting."
    reply = GUARANTEE_RE.sub(fix, reply).strip()

    # 4. The underwriting disclosure must precede any price.
    mentions_price = bool(re.search(r"\$\s?\d", reply))
    if "subject to medical underwriting" in reply.lower():
        session.underwriting_disclosed = True
    if mentions_price and not session.underwriting_disclosed:
        session.guard_events.append({"guard": "disclosure_injected"})
        reply = f"{UNDERWRITING_DISCLOSURE} {reply}"
        session.underwriting_disclosed = True
        extra.append(DISCLOSURE_REF)

    # 5. Code, not the model, decides what to ask next: drop any question the model added, append ours.
    #    Exception: objection handling may ask the playbook's own question (e.g. budget); ours waits a turn.
    if brain["intent"] == "objection" and "?" in reply:
        question = None
    if question:
        sentences = re.split(r"(?<=[.!?])\s+", reply.strip())
        while sentences and sentences[-1].endswith("?"):
            sentences.pop()
        reply = " ".join(sentences + [question]).strip()
    return reply, extra


# --------------------------------------------------------------------------- orchestrator

class LeadQualificationAgent:
    def __init__(self, kb: KnowledgeBase, brain, crm: MockCRM | None = None, log_dir: Path | None = CALL_LOG_DIR) -> None:
        self.kb, self.brain, self.crm, self.log_dir = kb, brain, crm or MockCRM(), log_dir
        self.sessions: dict[str, CallSession] = {}

    def start(self, call_id: str, market: str = "en") -> dict:
        session = CallSession(call_id=call_id, market=market)
        self.sessions[call_id] = session
        session.add("agent", GREETING, citations=[DISCLOSURE_REF])
        self._persist(session)
        return self._response(session, GREETING, [DISCLOSURE_REF])

    def session(self, call_id: str, market: str = "en") -> CallSession:
        if call_id not in self.sessions:
            self.start(call_id, market)
        return self.sessions[call_id]

    def turn(self, call_id: str, text: str, market: str = "en") -> dict:
        session = self.session(call_id, market)
        session.add("customer", text)
        if session.ended:
            return self._response(session, "This call has ended.", [])

        just_consented = session.recording_consent is None
        session.qualifying_started_turn = False
        # While an answer is disputed, the next reply is about that field.
        expected = next(iter(session.conflicts), None) or next_field(session)
        context = self.kb.retrieve(text, grounded_only=True).chunks
        try:
            brain = self.brain.converse(session, text, context, expected, RULES)
        except Exception as exc:  # provider outage: degrade to the rule-based brain, never crash the call
            session.guard_events.append({"guard": "llm_error", "error": type(exc).__name__, "detail": str(exc)[:300]})
            brain = fallback_brain(session, text, context, expected)

        # Deterministic overrides: these decisions are too important to leave to the model alone.
        if HUMAN_RE.search(text):
            brain["intent"] = "human_request"
        elif brain.get("intent") == "human_request" and not PERSON_RE.search(text):
            brain["intent"] = "provide_info"  # the model may not escalate on its own initiative
        if session.recording_consent is None:
            consent = brain.get("recording_consent")
            if DECLINE_RECORDING_RE.search(text):
                consent = False
            session.recording_consent = consent if consent is not None else True

        if session.recording_consent is False:
            session.outcome = evaluate_outcome(session)
            session.ended = True
            session.actions.append(self.crm.schedule_callback(call_id, {
                "reason": "Caller declined recording; human callback requested", "with": "human advisor"}))
            return self._finish(session, DECLINED_RECORDING_REPLY, [DISCLOSURE_REF])

        intent = brain.get("intent")
        # Routing intents are confirmed by surface patterns so a model slip cannot transfer or end a call.
        if EXISTING_RE.search(text):
            intent = "existing_member"
        elif COMPLAINT_RE.search(text):
            intent = "complaint"
        elif intent in {"existing_member", "complaint", "not_interested"} and not (
                EXISTING_RE.search(text) or COMPLAINT_RE.search(text) or NOT_INTERESTED_RE.search(text)):
            intent = "provide_info"
        brain["intent"] = intent

        if intent == "human_request":
            return self._escalate(session, "Caller asked for a human advisor", ESCALATION_REPLY, text)
        if intent == "existing_member":
            return self._escalate(session, "Existing member request (claims, billing or policy service)",
                                  EXISTING_MEMBER_REPLY, text)
        if intent == "complaint":
            return self._escalate(session, "Caller raised a complaint", COMPLAINT_REPLY, text)
        if intent == "not_interested" and session.mode == "discovery":
            session.outcome = {"status": "not_interested", "reason": "Caller was not interested in coverage"}
            session.ended = True
            return self._finish(session, NOT_INTERESTED_REPLY, [])

        # Mode switching: qualification starts only when the caller wants it (or volunteers details).
        if session.mode == "qualifying" and (intent == "decline_questions" or DECLINE_QUESTIONS_RE.search(text)):
            session.mode = "discovery"
        before = dict(session.lead)
        extracted, overridden = reconcile(brain.get("extracted") or {}, text, expected, intent)
        if overridden:
            session.guard_events.append({"guard": "caller_words_override", "field": expected,
                                         "model": brain.get("extracted", {}).get(expected), "used": extracted[expected]})
        merge_fields(session, extracted, text, expected, intent)
        if brain.get("recovered_prose"):
            session.guard_events.append({"guard": "recovered_prose"})
        volunteered = session.lead != before
        wants_quote = intent == "wants_quote" or QUOTE_RE.search(text) or (session.quote_offered and YES_RE.search(text)
                                                                        and intent not in {"ask_question", "objection"})
        if session.mode == "discovery" and (wants_quote or volunteered):
            session.mode = "qualifying"
            session.qualifying_started_turn = not volunteered  # intro only when the caller asked for a quote
        # A location outside the US ends the call, so never act on a single noisy transcript: ask once.
        state = session.lead.get("state")
        if isinstance(state, str) and state.endswith("(outside the US)") and session.pending_non_us is None:
            session.pending_non_us = state
            del session.lead["state"]
            session.guard_events.append({"guard": "non_us_unconfirmed", "heard": state})
        elif session.pending_non_us:
            # Answered: a US state replaces it, anything else confirms what we heard the first time.
            if "state" not in session.lead:
                session.lead["state"] = session.pending_non_us
            session.pending_non_us = None
        session.outcome = evaluate_outcome(session)

        # Nobody should be asked the same thing four times: move on, or hand to an advisor.
        pending = next_field(session)
        if pending and pending == session.last_asked and not session.pending_non_us and intent in ANSWERING:
            session.attempts[pending] = session.attempts.get(pending, 1) + 1
            if session.attempts[pending] >= 3:
                session.guard_events.append({"guard": "field_abandoned", "field": pending})
                if pending in RULES["required_for_outcome"]:
                    return self._escalate(session, f"Could not capture {pending.replace('_', ' ')} over the phone",
                                          ESCALATION_REPLY, text)
                session.lead[pending] = "not provided"
                session.outcome = evaluate_outcome(session)
        session.last_asked = next_field(session)
        self._sync_lead(session)

        cited = [context[i] for i in brain.get("citations", []) if isinstance(i, int) and 0 <= i < len(context)]
        if just_consented and session.mode == "discovery" and intent not in {"ask_question", "objection", "out_of_scope"}:
            brain["reply"] = "Thank you."
        question = None if brain.get("intent") == "end_call" else self._next_prompt(session)
        reply, extra_refs = guard_reply(session, brain.get("reply", ""), context, cited, brain, question)
        if session.outcome["status"] in {"not_eligible", "referred"} and session.outcome.get("source_ref"):
            extra_refs.append(session.outcome["source_ref"])  # outcomes cite the rule they apply
        refs = list(dict.fromkeys([c.source_ref for c in cited] + extra_refs))
        session.ended = session.outcome["status"] in {"not_eligible", "no_consent"} or brain.get("intent") == "end_call"
        session.add("agent", reply, citations=refs, intent=brain.get("intent"))
        self._persist(session)
        return self._response(session, reply, refs, intent=brain.get("intent"))

    def end(self, call_id: str) -> dict:
        session = self.sessions.get(call_id)
        if not session:
            return {"call_id": call_id, "status": "unknown call"}
        session.ended = True
        self._sync_lead(session, final=True)
        self._persist(session)
        return {"call_id": call_id, "outcome": session.outcome, "lead": session.lead, "actions": session.actions,
                "summary": crm_summary(session)}

    # ----------------------------------------------------------------------- helpers

    def _next_prompt(self, session: CallSession) -> str | None:
        if session.conflicts:
            name, values = next(iter(session.conflicts.items()))
            label = name.replace("_", " ")
            return (f"Just to make sure I have this right, earlier I noted your {label} as {display(name, values['previous'])}, "
                    f"and just now I heard {display(name, values['new'])}. Which one is correct?")
        status = session.outcome["status"]
        if status == "not_eligible":
            return (f"I'm sorry, {session.outcome['reason'][0].lower()}{session.outcome['reason'][1:]}, so I can't "
                    "prepare a quote today. Thank you for calling Darwix Health.")
        if status == "no_consent":
            return "No problem, we won't contact you. Thank you for calling Darwix Health."
        if session.mode == "discovery":
            if len([t for t in session.transcript if t["role"] == "customer"]) <= 1:
                return DISCOVERY_PROMPT
            session.quote_offered = True
            return OFFER_PROMPT
        name = next_field(session)
        if name == "state" and session.pending_non_us:
            return "I want to make sure I heard that right. Which state or country are you calling from?"
        if name and session.qualifying_started_turn:
            return f"{START_QUALIFYING} {RULES['questions'][name]}"
        if name is None:
            plans = " or ".join(session.outcome.get("plans", [])) or "one of our plans"
            if status == "referred":
                return ("Thank you. Because of the condition you mentioned, a senior health advisor will call you "
                        "to go through your options. Is there anything else I can help with?")
            return (f"Thank you, that's everything I need. Based on what you've told me, you may be a fit for {plans}. "
                    "A licensed advisor will call you with a personalized quote. Is there anything else I can help with?")
        return RULES["questions"][name]

    def _sync_lead(self, session: CallSession, final: bool = False) -> None:
        """Create/update the CRM lead once the caller consented; schedule the callback when its time is known."""
        if not session.lead.get("consent_to_contact") and not (final and session.lead):
            return
        status = session.outcome["status"]
        action = self.crm.upsert_lead(session.call_id, {
            "status": status, "plans": session.outcome.get("plans", []), "reason": session.outcome.get("reason"),
            "fields": session.lead, "summary": crm_summary(session)})
        if not any(a["type"] == "lead" for a in session.actions):
            session.actions.append(action)
        if session.lead.get("callback_time") and session.lead.get("consent_to_contact") and \
                not any(a["type"] == "callback" for a in session.actions):
            session.actions.append(self.crm.schedule_callback(session.call_id, {
                "preferred_time": session.lead["callback_time"],
                "with": "senior health advisor" if status == "referred" else "licensed advisor"}))

    def _escalate(self, session: CallSession, reason: str, reply: str, text: str) -> dict:
        session.escalated = True
        session.outcome = {"status": "escalated", "reason": reason, "source_ref": ESCALATION_REF}
        session.actions.append(self.crm.escalate(session.call_id, {
            "reason": reason, "last_customer_text": text, "lead": session.lead}))
        self._sync_lead(session)
        session.ended = True
        return self._finish(session, reply, [ESCALATION_REF])

    def _finish(self, session: CallSession, reply: str, refs: list[str]) -> dict:
        session.add("agent", reply, citations=refs)
        self._persist(session)
        return self._response(session, reply, refs)

    def _persist(self, session: CallSession) -> None:
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / f"{session.call_id}.json").write_text(json.dumps(session.snapshot(), indent=2))

    @staticmethod
    def _response(session: CallSession, text: str, citations: list[str], intent: str | None = None) -> dict:
        return {"call_id": session.call_id, "text": text, "citations": citations, "intent": intent,
                "escalated": session.escalated, "end_call": session.ended, "lead": session.lead,
                "conflicts": session.conflicts, "outcome": session.outcome, "actions": session.actions,
                "guard_events": session.guard_events[-3:]}


def display(name: str, value) -> str:
    if name == "state":
        return STATE_NAMES.get(value, value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def crm_summary(session: CallSession) -> str:
    lead, outcome = session.lead, session.outcome
    parts = [f"Outcome: {outcome['status'].replace('_', ' ')}"]
    if outcome.get("reason"):
        parts.append(outcome["reason"])
    if outcome.get("plans"):
        parts.append("Possible plans: " + ", ".join(outcome["plans"]))
    if lead:
        parts.append("Details: " + "; ".join(f"{k.replace('_', ' ')}={display(k, v)}" for k, v in lead.items()))
    if session.conflicts:
        parts.append("Unresolved: " + ", ".join(session.conflicts))
    return ". ".join(parts) + "."
