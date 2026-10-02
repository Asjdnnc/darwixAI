"""Q3 reminder agent for the Philippines and Indonesia markets.

A renewal or installment reminder is a servicing call, not a qualification call: it ends in a
promise to pay, an already-paid note, a hardship referral, a dispute, or a refusal. None of those
exist in the Q1 flow, so this agent has its own turn pipeline and reuses only what is genuinely
shared with Q1 — session state, the ungrounded-number guard, the mock CRM and call persistence.

Everything the agent says comes from that market's flow in `app/flows.py`, which in turn mirrors
the approved call playbook stored in the market knowledge base.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.agent import (CALL_LOG_DIR, NUMBER_RE, OFFER_HUMAN, UNAVAILABLE, CallSession, allowed_numbers)
from app.crm import MockCRM
from app.flows import Flow, flow
from app.kb import KnowledgeBase

# A question is detected by the local question words, not by English ones.
QUESTION_WORDS = {
    "ph": r"\b(ano|anong|bakit|paano|magkano|kailan|saan|sino|pwede ba|ilan|may)\b",
    "id": r"\b(apa|apakah|kenapa|mengapa|bagaimana|gimana|berapa|kapan|di ?mana|siapa|bisa ?kah|boleh ?kah)\b",
}

# Payment channels each market actually uses, for extracting a promise.
CHANNELS = {
    "ph": {
        # Whisper drops the leading G ("GCash" -> "Gash"/"cash") in both synthetic and recorded
        # Taglish, so the shortened forms are accepted too.
        "GCash": r"\bg[\s-]?cash\b|\bgi[\s-]?cash\b|\bji[\s-]?cash\b|\bgcash\b|\bgash\b",
        "Maya": r"\bmaya\b|\bpaymaya\b",
        "bank branch": r"\bbpi\b|\bbdo\b|\bmetrobank\b|\blandbank\b|\bsecurity bank\b|\bbangko\b|\bbank\b",
        "Bayad Center": r"\bbayad center\b|\bbayad\b", "SM Bills Payment": r"\bsm\b",
        "Palawan Express": r"\bpalawan\b", "7-Eleven": r"\b7-?eleven\b|\bcliqq\b",
        "online banking": r"\bonline\b|\binstapay\b|\bpesonet\b|\bapp\b",
    },
    "id": {
        "virtual account": r"\bvirtual account\b|\bva\b|\bbca\b|\bmandiri\b|\bbni\b|\bbri\b|\btransfer\b",
        "Indomaret": r"\bindomaret\b", "Alfamart": r"\balfamart\b",
        "GoPay": r"\bgopay\b", "OVO": r"\bovo\b", "DANA": r"\bdana\b", "ShopeePay": r"\bshopee ?pay\b",
        "autodebet": r"\bauto ?deb[ei]t\b",
    },
}

# Payment-date expressions, including payday-linked promises, which are the norm in both markets:
# Filipino kinsenas/katapusan (15th / end of month) and Indonesian gajian (payday).
DATE_PHRASES = {
    # "sa" + any word would swallow the channel ("Sa GCash po"), so only real time words follow it.
    # Whisper writes Taglish with Spanish-influenced spelling, so "kinsenas" comes back as
    # "quincenas". Both spellings are accepted; observed in the PH-1 recording.
    "ph": r"\b(?:sa\s+)?(bukas|ngayon|mamaya|makalawa|kinsenas|quin[cs]enas|kin[cs]enas|katapusan|"
          r"catapusan|sahod|sweldo|payday|next week|this week|lunes|martes|miyerkules|huwebes|"
          r"biyernes|sabado|linggo|\d{1,2}(?:st|nd|rd|th)?)\b",
    "id": r"(besok|lusa|hari ini|nanti|minggu depan|akhir bulan|tanggal\s*\d{1,2}|gajian|senin|selasa|rabu|"
          r"kamis|jum'?at|sabtu|minggu|\d{1,2}\s*hari lagi)",
}


POLITENESS_PH = re.compile(r"\bpong\b", re.I), re.compile(r"\b(?:po|ho|opo)\b", re.I)


def strip_politeness(market: str, text: str) -> str:
    """Filipino inserts 'po'/'ho' anywhere in a sentence as a respect marker, so intent patterns
    would otherwise need a variant for every position: "wala po akong pera" vs "wala akong pera".
    The particle carries no meaning for intent, so it is removed before matching."""
    if market != "ph":
        return text
    pong, particle = POLITENESS_PH
    stripped = particle.sub(" ", pong.sub("ng", text))
    return re.sub(r"\s+", " ", stripped).strip()


def detect_intent(flw: Flow, text: str) -> str | None:
    """Deterministic read of the caller's words; the model never overrides these."""
    candidate = strip_politeness(flw.market, text)
    for name in ("decline_recording", "wrong_person", "already_paid", "human_request", "dispute",
                 "hardship", "refuse", "promise"):
        if flw.intents[name].search(text) or flw.intents[name].search(candidate):
            return name
    return None


def extract_payment(market: str, text: str) -> dict:
    # Whisper punctuates mid-phrase in Taglish ("sa Bayad? Center"), which would split a channel
    # name in two, so punctuation is flattened before matching.
    text = re.sub(r"[.,!?;:]+", " ", text)
    found = {}
    for label, pattern in CHANNELS[market].items():
        if re.search(pattern, text, re.I):
            found["payment_channel"] = label
            break
    match = re.search(DATE_PHRASES[market], text, re.I)
    if match:
        # Keep only the time expression itself, so the closing line reads naturally.
        found["payment_date"] = (match.group(1) if match.groups() else match.group(0)).strip()
    return found


# If a reply of any length contains none of these, it is not in the caller's language any more.
LANGUAGE_MARKERS = {
    "ph": re.compile(r"\b(po|opo|ninyo|niyo|ang|ng|sa|ko|kayo|natin|namin|naming|ay|mga|ito|iyan|kung|"
                     r"salamat|pasensya|maaari|pwede|hindi|oo)\b", re.I),
    "id": re.compile(r"\b(yang|dan|untuk|dengan|saya|kami|anda|bapak|ibu|pak|bu|bisa|dapat|tidak|sudah|"
                     r"belum|akan|terima kasih|mohon|silakan|ya|ini|itu|pada|dari)\b", re.I),
}


def switched_language(market: str, text: str) -> bool:
    """True when the agent has drifted into English, which the market brief forbids."""
    words = re.findall(r"[A-Za-z']+", text)
    return len(words) >= 5 and not LANGUAGE_MARKERS[market].search(text)


def is_question(market: str, text: str) -> bool:
    return bool(text.strip().endswith("?") or re.search(QUESTION_WORDS[market], text, re.I))


class ReminderAgent:
    """Outbound renewal (ph) / installment (id) reminder call."""

    def __init__(self, kbs: dict[str, KnowledgeBase], brain, crm: MockCRM | None = None,
                 log_dir: Path | None = CALL_LOG_DIR) -> None:
        self.kbs, self.brain = kbs, brain
        self.crm, self.log_dir = crm or MockCRM(), log_dir
        self.sessions: dict[str, CallSession] = {}

    # ----------------------------------------------------------------- call lifecycle

    def start(self, call_id: str, market: str) -> dict:
        flw = flow(market)
        session = CallSession(call_id=call_id, market=market)
        session.outcome = {"status": "in_progress"}
        self.sessions[call_id] = session
        session.add("agent", flw.greeting)
        self._persist(session)
        return self._response(session, flw.greeting, [])

    def turn(self, call_id: str, text: str, market: str = "ph") -> dict:
        session = self.sessions.get(call_id) or self.start(call_id, market) and self.sessions[call_id]
        flw = flow(session.market)
        session.add("customer", text)
        if session.ended:
            return self._response(session, "", [])

        intent = detect_intent(flw, text)

        # Recording consent is settled on the first caller turn.
        if session.recording_consent is None:
            session.recording_consent = intent != "decline_recording"
            if not session.recording_consent:
                self.crm.schedule_callback(call_id, {"reason": "Caller declined recording", "with": "licensed advisor"})
                session.actions.append({"type": "callback", "id": f"cb_{call_id[:8]}", "operation": "created"})
                return self._close(session, "recording_declined", flw)

        # Deterministic routes that end or hand off the call.
        if intent == "wrong_person":
            return self._close(session, "wrong_person", flw)
        if intent == "already_paid":
            self.crm.record("notes", call_id, {"market": session.market, "note": "Caller states payment already made",
                                               "action": "no further reminder; await posting"})
            session.actions.append({"type": "note", "id": f"note_{call_id[:8]}", "operation": "created"})
            return self._close(session, "already_paid", flw)
        if intent in {"human_request", "dispute", "hardship"}:
            reason = {"human_request": "Caller asked for a human agent",
                      "dispute": "Caller disputes the amount or the record",
                      "hardship": "Caller reports payment difficulty; refer for keringanan / payment-mode review"}[intent]
            session.escalated = True
            self.crm.escalate(call_id, {"reason": reason, "market": session.market, "last_customer_text": text})
            session.actions.append({"type": "escalation", "id": f"esc_{call_id[:8]}", "operation": "created"})
            status = "hardship_referred" if intent == "hardship" else ("disputed" if intent == "dispute" else "escalated")
            return self._close(session, status, flw, refs=[flw.escalation_ref])
        if intent == "refuse":
            return self._close(session, "refused", flw)

        # Ordinary turn: answer from the market knowledge base, then take the payment commitment.
        session.lead.update(extract_payment(session.market, text))
        context = self.kbs[session.market].retrieve(text, grounded_only=True).chunks
        expected = next((f for f in flw.collect_order if f not in session.lead), None)
        try:
            brain = self.brain.converse_reminder(session, text, context, expected, flw)
        except Exception as exc:
            session.guard_events.append({"guard": "llm_error", "error": type(exc).__name__, "detail": str(exc)[:200]})
            brain = {"reply": "", "citations": [], "intent": "provide_info", "extracted": {}}

        cited = [context[i] for i in brain.get("citations", []) if isinstance(i, int) and 0 <= i < len(context)]
        if not session.transcript[-2:-1] or len([t for t in session.transcript if t["role"] == "customer"]) == 1:
            prompt = flw.reason  # first substantive turn states why we are calling
        elif session.lead.get("payment_channel") and session.lead.get("payment_date"):
            return self._promise(session, flw)
        else:
            prompt = flw.questions[expected] if expected else flw.questions[flw.collect_order[0]]

        reply, refs = self._guard(session, flw, brain.get("reply", ""), context, text, prompt)
        refs = list(dict.fromkeys([c.source_ref for c in cited] + refs))
        session.add("agent", reply, citations=refs, intent=brain.get("intent"))
        self._persist(session)
        return self._response(session, reply, refs)

    def end(self, call_id: str) -> dict:
        session = self.sessions.get(call_id)
        if not session:
            return {"call_id": call_id, "status": "unknown call"}
        session.ended = True
        self._persist(session)
        return {"call_id": call_id, "market": session.market, "outcome": session.outcome,
                "captured": session.lead, "actions": session.actions}

    # ----------------------------------------------------------------- helpers

    def _promise(self, session: CallSession, flw: Flow) -> dict:
        self.crm.record("promises", session.call_id, {
            "market": session.market, "channel": session.lead["payment_channel"],
            "date": session.lead["payment_date"], "flow": flw.name})
        session.actions.append({"type": "promise", "id": f"ptp_{session.call_id[:8]}", "operation": "created"})
        return self._close(session, "promise_to_pay", flw)

    def _close(self, session: CallSession, status: str, flw: Flow, refs: list | None = None) -> dict:
        reply = flw.script[status].format(**{k: session.lead.get(k, "") for k in flw.collect_order})
        session.outcome = {"status": status, "flow": flw.name, "market": session.market}
        session.ended = True
        session.add("agent", reply, citations=refs or [])
        self._persist(session)
        return self._response(session, reply, refs or [])

    def _guard(self, session: CallSession, flw: Flow, reply: str, context: list, caller_text: str,
               prompt: str) -> tuple[str, list]:
        """Market-aware safety net. Mirrors the Q1 guards in the caller's own language."""
        extra: list = []
        unavailable = f"{UNAVAILABLE[session.market]} {OFFER_HUMAN[session.market]}"

        # 1. A question with no retrieved evidence is answered honestly, in-language.
        if is_question(session.market, caller_text) and not context:
            session.guard_events.append({"guard": "unsupported_question", "original": reply})
            reply = unavailable

        # 2. Amounts (denda, premium, totals) must never be invented: they come from the system.
        invented = [n for n in NUMBER_RE.findall(reply)
                    if n.strip("$%").replace(",", "").replace(".", "") not in
                    {a.replace(".", "") for a in allowed_numbers(session, context)}]
        if invented:
            session.guard_events.append({"guard": "ungrounded_number", "numbers": invented, "original": reply})
            reply = unavailable if is_question(session.market, caller_text) else ""

        # 3. Market compliance rewrites (PH: grace-period wording, reinstatement promises;
        #    ID: OJK collections conduct, guaranteed keringanan approval).
        for pattern, replacement in flw.prohibited:
            if pattern.search(reply):
                session.guard_events.append({"guard": "prohibited_statement", "original": reply})
                reply = pattern.sub(replacement, reply).strip()
                extra.append(flw.policy_refs["conduct"])

        # 3b. The playbooks use slots like "[grace end date]" and "[nominal]"; a model that echoes
        #     one would read it aloud, so any sentence still containing a placeholder is dropped.
        if re.search(r"\[[^\]]{2,40}\]", reply):
            session.guard_events.append({"guard": "unfilled_placeholder", "original": reply})
            reply = " ".join(x for x in re.split(r"(?<=[.!?])\s+", reply)
                             if not re.search(r"\[[^\]]{2,40}\]", x)).strip()

        # 4. Staying in the caller's language is a hard requirement of this flow, so an English
        #    sentence is dropped rather than spoken.
        if reply.strip() and switched_language(session.market, reply):
            session.guard_events.append({"guard": "language_switch", "original": reply})
            reply = unavailable if is_question(session.market, caller_text) else ""

        # 5. Never repeat the previous reply word for word.
        previous = next((t["text"] for t in reversed(session.transcript[:-1]) if t["role"] == "agent"), "")
        if reply.strip() and reply.strip() in previous:
            session.guard_events.append({"guard": "repeated_reply"})
            reply = ""

        # 6. Code decides what is asked next; drop any question the model appended.
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s and not s.endswith("?")]
        return " ".join(sentences + [prompt]).strip(), extra

    def _persist(self, session: CallSession) -> None:
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        import json
        (self.log_dir / f"{session.call_id}.json").write_text(json.dumps(session.snapshot(), indent=2))

    @staticmethod
    def _response(session: CallSession, text: str, citations: list) -> dict:
        return {"call_id": session.call_id, "market": session.market, "text": text, "citations": citations,
                # "lead" is the key the browser panel reads for every market; "captured" is its
                # flow-accurate name here (a reminder captures a commitment, not a lead).
                "outcome": session.outcome, "captured": session.lead, "lead": session.lead,
                "conflicts": {}, "actions": session.actions,
                "escalated": session.escalated, "end_call": session.ended,
                "guard_events": session.guard_events[-3:]}
