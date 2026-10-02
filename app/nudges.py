"""Q4 nudge generation and suppression.

A nudge is only useful if the agent can act on it mid-call, so the hard problem is not generating
them but *not* generating them. Every control the brief asks for is applied here:

| Control | How |
|---|---|
| Confidence threshold | below `NUDGE_MIN_CONFIDENCE` the signal is dropped |
| Cooldown | the same topic cannot fire twice within its cooldown window |
| Duplicate suppression | a near-identical message is suppressed even across topics |
| Topic grouping | one nudge per coaching theme at a time (compliance, relationship, …) |
| Priority | compliance outranks relationship outranks opportunity |
| Expiry | nudges age out so the dashboard shows only what is still actionable |
| Repetition limit | a topic can fire at most `MAX_PER_TOPIC` times per call |

Every suppression is recorded with its reason, which is what makes the false-positive analysis in
`docs/Q4_REALTIME.md` possible.
"""
from __future__ import annotations

from datetime import timedelta

from app.config import settings
from app.models import Nudge, Signal, utc_now
from app.signals import GROUP, PRIORITY

MESSAGES = {
    "compliance_gap": "Give the underwriting disclosure before discussing price.",
    "risky_statement": "Do not promise approval or coverage — say it depends on underwriting.",
    "frustration": "Acknowledge the frustration before continuing.",
    "payment_difficulty": "Offer an approved payment-support or callback path; promise no exception.",
    "callback": "Confirm a callback time and consent before ending.",
    "missed_cross_sell": "Ask whether the family members mentioned should be covered too.",
    "buying_signal": "Move to next steps — confirm details and send the application.",
    "topic_shift": "Acknowledge the new topic and park the previous one.",
}

# Seconds before the same topic may fire again. Compliance repeats sooner because the cost of
# missing it is higher than the cost of repeating it.
COOLDOWN = {
    "compliance_gap": 45, "risky_statement": 45,
    "frustration": 90, "payment_difficulty": 120,
    "callback": 120, "missed_cross_sell": 150, "buying_signal": 90, "topic_shift": 60,
}
EXPIRY = {"compliance_gap": 60, "risky_statement": 60, "buying_signal": 45}
DEFAULT_EXPIRY = 90
MAX_PER_TOPIC = 2
MAX_ACTIVE = 3
# An inferred signal has no explicit evidence behind it, so it must clear a higher bar. Measured:
# without this the LLM flagged "I'm looking at health cover" as a buying signal mid-discovery.
LLM_CONFIDENCE_PENALTY = 0.10
# Each compliance finding is a distinct regulatory event, so unlike coaching themes they are not
# collapsed into one: a missing disclosure and an unlawful promise both need saying.
UNGROUPED = {"compliance"}


def _similar(a: str, b: str) -> bool:
    x, y = set(a.lower().split()), set(b.lower().split())
    return bool(x) and len(x & y) / len(x | y) >= 0.6


class NudgeEngine:
    def __init__(self) -> None:
        self.history: dict[str, list[Nudge]] = {}
        self.suppressed: dict[str, list[dict]] = {}

    # ----------------------------------------------------------------- state

    def active(self, call_id: str) -> list[Nudge]:
        """Nudges that have not yet expired, highest priority first."""
        now = utc_now()
        live = [n for n in self.history.get(call_id, [])
                if now - n.created_at < timedelta(seconds=n.expires_in_seconds)]
        return sorted(live, key=lambda n: (PRIORITY.get(n.topic, 9), n.created_at))[:MAX_ACTIVE]

    def _suppress(self, call_id: str, signal: Signal, reason: str) -> None:
        self.suppressed.setdefault(call_id, []).append(
            {"topic": signal.topic, "confidence": signal.confidence, "reason": reason,
             "evidence": signal.evidence, "at": utc_now().isoformat(timespec="seconds")})

    # ----------------------------------------------------------------- creation

    def create(self, call_id: str, signal: Signal) -> Nudge | None:
        if signal.topic not in MESSAGES:
            return None
        threshold = settings.nudge_min_confidence + (LLM_CONFIDENCE_PENALTY if signal.source == "llm" else 0)
        if signal.confidence < threshold:
            self._suppress(call_id, signal, "below_confidence_threshold")
            return None

        now = utc_now()
        prior = self.history.setdefault(call_id, [])
        same_topic = [n for n in prior if n.topic == signal.topic]

        if len(same_topic) >= MAX_PER_TOPIC:
            self._suppress(call_id, signal, "repetition_limit")
            return None
        if same_topic and now - same_topic[-1].created_at < timedelta(seconds=COOLDOWN.get(signal.topic, 90)):
            self._suppress(call_id, signal, "cooldown")
            return None

        message = MESSAGES[signal.topic]
        if any(_similar(message, n.message) for n in self.active(call_id)):
            self._suppress(call_id, signal, "duplicate_message")
            return None

        # One nudge per coaching theme at a time: three compliance alerts help nobody.
        group = GROUP.get(signal.topic)
        live_group = [n for n in self.active(call_id) if GROUP.get(n.topic) == group]
        if live_group and group not in UNGROUPED:
            incoming, existing = PRIORITY.get(signal.topic, 9), min(PRIORITY.get(n.topic, 9) for n in live_group)
            if incoming >= existing:
                self._suppress(call_id, signal, "topic_group_occupied")
                return None

        nudge = Nudge(call_id=call_id, topic=signal.topic, message=message, confidence=signal.confidence,
                      expires_in_seconds=EXPIRY.get(signal.topic, DEFAULT_EXPIRY))
        prior.append(nudge)
        return nudge

    # ----------------------------------------------------------------- reporting

    def summary(self, call_id: str) -> dict:
        fired = self.history.get(call_id, [])
        dropped = self.suppressed.get(call_id, [])
        reasons: dict[str, int] = {}
        for item in dropped:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        return {"fired": len(fired), "suppressed": len(dropped), "suppression_reasons": reasons,
                "topics": sorted({n.topic for n in fired})}
