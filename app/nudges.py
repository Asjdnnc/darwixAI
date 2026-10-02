from datetime import timedelta
from app.config import settings
from app.models import Nudge, Signal, utc_now

MESSAGES = {
    "compliance_gap": "Confirm the required disclosure before proceeding.",
    "cross_sell": "Ask whether the customer wants a family health insurance plan.",
    "frustration": "Acknowledge the concern, apologize briefly, and clarify the next step.",
    "payment_difficulty": "Offer an approved payment-support or callback path; do not promise an unapproved exception.",
    "callback": "Confirm the preferred callback date, time, and consent.",
}


class NudgeEngine:
    def __init__(self) -> None:
        self.recent: dict[tuple[str, str], Nudge] = {}

    def create(self, call_id: str, signal: Signal) -> Nudge | None:
        if signal.confidence < settings.nudge_min_confidence:
            return None
        key = (call_id, signal.topic)
        prior = self.recent.get(key)
        if prior and utc_now() - prior.created_at < timedelta(seconds=prior.expires_in_seconds):
            return None
        nudge = Nudge(call_id=call_id, topic=signal.topic, message=MESSAGES[signal.topic], confidence=signal.confidence)
        self.recent[key] = nudge
        return nudge
