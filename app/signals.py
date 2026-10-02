"""Q4 signal extraction from a live call transcript.

This watches a *human* agent on a call and looks for moments worth a nudge. Two stages:

1. **Deterministic rules** run on every chunk. They are fast (microseconds), explainable, and act as
   the test oracle. Several are stateful — a missing disclosure can only be judged against what has
   already been said in this call.
2. **An optional LLM pass** adds nuance the rules miss (implied frustration, indirect buying
   signals). It is skipped for chunks the rules already explain, and its output is clamped to the
   same taxonomy, so a provider outage degrades coverage but never breaks the pipeline.

Confidence is deliberately conservative: a rule with explicit evidence scores high, an inferred
signal scores low enough that the nudge gate drops it unless corroborated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Signal

# Priority drives ordering and pre-emption when several nudges are live at once.
PRIORITY = {
    "compliance_gap": 1,       # regulatory exposure first
    "risky_statement": 1,
    "payment_difficulty": 2,
    "frustration": 2,
    "callback": 3,
    "missed_cross_sell": 3,
    "buying_signal": 2,   # missing a close is costly, so it outranks other opportunity signals
    "topic_shift": 4,
}

# Topics that belong to the same coaching theme are grouped so the agent sees one, not three.
GROUP = {
    "compliance_gap": "compliance", "risky_statement": "compliance",
    "frustration": "relationship", "payment_difficulty": "relationship",
    "missed_cross_sell": "opportunity", "buying_signal": "opportunity",
    "callback": "logistics", "topic_shift": "logistics",
}


def _any(text: str, *patterns: str) -> re.Match | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match
    return None


# --------------------------------------------------------------------------- call state

@dataclass
class CallState:
    """What has happened so far in this call; several signals are only meaningful against it."""
    disclosure_given: bool = False
    price_mentioned: bool = False
    consent_asked: bool = False
    frustration_hits: int = 0
    mentioned_dependents: bool = False
    cross_sell_offered: bool = False
    callback_captured: bool = False
    topics: list = field(default_factory=list)
    chunks: int = 0


DISCLOSURE_RE = r"subject to (medical )?underwriting|depends on verified medical history|underwriting and depend"
PRICE_RE = r"\$\s?\d|\b\d+\s*(?:dollars|pesos|rupiah)\b|\bper month\b|\bpremium is\b|\bcosts?\s+\$?\d"
RISKY_RE = (r"\bguarantee[sd]?\b|\byou(?:'re| are| will be) (?:definitely )?(?:approved|covered)\b|"
            r"\bno medical (?:exam|check)\b|\b100%\s*(?:approved|covered)\b|\bdon'?t worry about the\b")
# "terrible"/"awful" are often about something other than the call ("the weather has been
# terrible"), so they only count alongside a service complaint; the rest are unambiguous.
STRONG_FRUSTRATION_RE = (r"\b(?:ridiculous|unacceptable|angry|furious|fed up)\b|"
                         r"\bwaste of (?:my )?time\b|"
                         r"\b(?:terrible|awful|appalling)\b(?=[^.!?]*\b(?:service|support|company|experience|"
                         r"you|your|staff|team)\b)")
MILD_FRUSTRATION_RE = (r"\bfrustrat\w+|annoyed\b|\b(?:i've|i have) (?:already )?(?:told you|explained)\b|"
                       r"\bthird time\b|\bagain and again\b|\bstill waiting\b")
# A *person* is only an opportunity when something suggests they need cover ("my wife says hello"
# is not a lead). A second *asset* is the opportunity in itself — it is the brief's own example.
DEPENDENT_RE = r"\b(?:my |our )?(?:wife|husband|spouse|partner|kids?|children|son|daughter|family)\b"
ASSET_RE = (r"\b(?:second|another|a new|additional) (?:car|vehicle|house|home|property|bike|motorbike|"
            r"policy|van|truck)\b")
# A dependent is only an opportunity when something suggests they need cover. Without this,
# "my wife says hello" and "I was reading about your family plan" both fired.
COVER_NEED_RE = (r"\b(?:cover|covered|covering|coverage|insure[d]?|insurance|policy|plan|quote|premium|"
                 r"add|include|protect|needs?|too|as well|also|lost (?:her|his|their))\b")
# "family plan"/"Family Shield" is a product name, not a person who needs covering.
PRODUCT_NAME_RE = r"\b(?:family (?:plan|shield|policy|cover)|senior secure|essential care)\b"
BUYING_RE = (r"\b(?:how do i|how can i) (?:sign up|enrol|apply|start)\b|\bi'?m (?:ready|interested)\b|"
             r"\bsounds good\b|\blet'?s do it\b|\bsend me the (?:forms?|details|application)\b|\bwhen can i start\b")
PAYMENT_RE = (r"\b(?:can'?t|cannot|can not) (?:afford|pay|make the payment)\b|\btoo expensive\b|\bno money\b|"
              r"\btight (?:right now|this month)\b|\blost my job\b|\bbetween jobs\b|\bstruggling\b|"
              r"\bbehind on\b|\bdon'?t have the money\b")
CALLBACK_RE = r"\bcall me (?:back|later)\b|\bcall back\b|\bbad time\b|\bi'?m (?:driving|at work|busy)\b|\bring me\b"


def detect_rules(text: str, speaker: str, state: CallState) -> list[Signal]:
    """Deterministic pass. Speaker matters: the same words mean different things from each side."""
    found: list[Signal] = []
    state.chunks += 1

    if speaker == "agent":
        if _any(text, DISCLOSURE_RE):
            state.disclosure_given = True
        if _any(text, r"\bbest time to call\b|\bcall you back (?:on|at)\b"):
            state.callback_captured = True
        if _any(text, r"\bfamily (?:plan|shield)\b|\badd (?:your|them) to the policy\b"):
            state.cross_sell_offered = True

        # Compliance: a price stated before the mandatory disclosure.
        if _any(text, PRICE_RE):
            state.price_mentioned = True
            if not state.disclosure_given:
                found.append(Signal(topic="compliance_gap", confidence=0.95,
                                    evidence="Price quoted before the underwriting disclosure"))
        # Risk: a promise the agent is not allowed to make.
        if (match := _any(text, RISKY_RE)) and not _any(text, r"\b(can'?t|cannot|never|not able to)\b[^.!?]{0,20}guarantee"):
            found.append(Signal(topic="risky_statement", confidence=0.92,
                                evidence=f"Agent said {match.group(0)!r}, which cannot be promised"))
        # Opportunity: dependents were raised earlier and never followed up.
        if state.mentioned_dependents and not state.cross_sell_offered and state.chunks >= 2:
            found.append(Signal(topic="missed_cross_sell", confidence=0.78,
                                evidence="Customer mentioned family earlier; no multi-person plan offered yet"))
        return found

    # ----- customer side
    without_product = re.sub(PRODUCT_NAME_RE, " ", text, flags=re.I)
    person = _any(without_product, DEPENDENT_RE)
    asset = _any(without_product, ASSET_RE)
    if asset or (person and _any(without_product, COVER_NEED_RE)):
        state.mentioned_dependents = True
        if not state.cross_sell_offered:
            mention = (asset or person).group(0)
            found.append(Signal(topic="missed_cross_sell", confidence=0.86,
                                evidence=f"Customer mentioned {mention!r}"))
    # Explicit anger is actionable immediately; a repetition marker ("I've told you") only once it
    # actually repeats, which is what "rising" frustration means.
    if match := _any(text, STRONG_FRUSTRATION_RE):
        state.frustration_hits += 1
        found.append(Signal(topic="frustration", confidence=0.9,
                            evidence=f"{match.group(0)!r} (occurrence {state.frustration_hits})"))
    elif match := _any(text, MILD_FRUSTRATION_RE):
        state.frustration_hits += 1
        found.append(Signal(topic="frustration",
                            confidence=0.72 if state.frustration_hits == 1 else 0.9,
                            evidence=f"{match.group(0)!r} (occurrence {state.frustration_hits})"))
    if match := _any(text, PAYMENT_RE):
        found.append(Signal(topic="payment_difficulty", confidence=0.9,
                            evidence=f"Customer said {match.group(0)!r}"))
    if match := _any(text, BUYING_RE):
        found.append(Signal(topic="buying_signal", confidence=0.88,
                            evidence=f"Customer said {match.group(0)!r}"))
    if (match := _any(text, CALLBACK_RE)) and not state.callback_captured:
        found.append(Signal(topic="callback", confidence=0.85,
                            evidence=f"Customer said {match.group(0)!r}"))
    return found


# --------------------------------------------------------------------------- LLM second pass

LLM_PROMPT = """You watch a live sales/servicing call and flag only moments a human agent should act on.

Return ONLY JSON: {"signals": [{"topic": ..., "confidence": 0.0-1.0, "evidence": "<quote>"}]}

topic must be one of: compliance_gap, risky_statement, frustration, payment_difficulty, callback, \
missed_cross_sell, buying_signal, topic_shift.

Rules:
- Flag ONLY what is explicitly supported by the words in this chunk. Do not infer mood from topic.
- evidence must be a short quote from the chunk.
- If nothing clearly actionable is present, return {"signals": []}. That is the common case; silence \
is better than a weak alert.
- confidence above 0.8 only when the words are unambiguous."""

ALLOWED = set(PRIORITY)


def detect_llm(service, text: str, speaker: str, already: list[Signal]) -> list[Signal]:
    """Optional nuance pass. Returns [] on any failure, so the rules remain the floor."""
    if service is None or len(text.split()) < 4:
        return []
    try:
        raw = service.classify_signals(LLM_PROMPT, f"speaker={speaker}\nchunk: {text}")
    except Exception:
        return []
    seen = {s.topic for s in already}
    out = []
    for item in raw or []:
        topic = str(item.get("topic", ""))
        if topic not in ALLOWED or topic in seen:
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        # An inferred signal is held to a higher bar than an explicit rule match.
        out.append(Signal(topic=topic, confidence=min(confidence, 0.85), source="llm",
                          evidence=str(item.get("evidence", ""))[:200]))
    return out


def detect(text: str, speaker: str, state: CallState, service=None) -> list[Signal]:
    rules = detect_rules(text, speaker, state)
    return rules + detect_llm(service, text, speaker, rules)
