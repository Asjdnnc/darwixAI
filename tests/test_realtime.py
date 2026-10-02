"""Q4: live signal extraction, nudge suppression and the real-time pipeline. Offline."""
import time

import pytest

from app.models import Signal
from app.nudges import MAX_PER_TOPIC, NudgeEngine
from app.signals import CallState, detect_rules
from app.stream import RealtimePipeline, echoes_prompt


def signals(text, speaker="customer", state=None):
    return detect_rules(text, speaker, state or CallState())


def topics(text, speaker="customer", state=None):
    return {s.topic for s in signals(text, speaker, state)}


# --- the four scenarios the brief requires

def test_compliance_gap_when_price_precedes_the_disclosure():
    state = CallState()
    assert topics("Essential Care is $189 per month.", "agent", state) == {"compliance_gap"}
    # Once the disclosure has been given, the same sentence is clean.
    clean = CallState()
    detect_rules("Final coverage and premiums are subject to medical underwriting.", "agent", clean)
    assert topics("Essential Care is $189 per month.", "agent", clean) == set()


def test_risky_statement_is_flagged_but_a_denial_is_not():
    assert topics("Yes, you're definitely approved, don't worry.", "agent") == {"risky_statement"}
    assert "risky_statement" not in topics("I can't guarantee approval, it depends on underwriting.", "agent")


def test_missed_cross_sell_when_a_dependent_is_mentioned():
    state = CallState()
    assert topics("My wife just lost her employer plan too.", "customer", state) == {"missed_cross_sell"}
    # Suppressed once the agent has actually offered a multi-person plan.
    offered = CallState()
    detect_rules("We could look at Family Shield for both of you.", "agent", offered)
    assert topics("My wife needs cover as well.", "customer", offered) == set()


def test_rising_frustration_scores_higher_on_repetition():
    state = CallState()
    first = signals("I've already told you twice.", "customer", state)[0]
    second = signals("I've already told you again.", "customer", state)[0]
    assert first.confidence < second.confidence
    # Explicit anger is actionable on the first mention.
    assert signals("This is ridiculous.", "customer", CallState())[0].confidence >= 0.9


def test_noisy_or_ambiguous_speech_produces_no_signal():
    for text in ["Sorry, can you hear me? The line is breaking up.", "Hello? Hello.",
                 "Right, sorry, I was asking about the thing we discussed.",
                 "Never mind, it's fine, nothing urgent.", "Mmm. Okay. Yeah."]:
        assert signals(text) == [], text


# --- nudge suppression controls

def test_confidence_threshold_and_the_extra_bar_for_inferred_signals():
    engine = NudgeEngine()
    assert engine.create("c", Signal(topic="frustration", confidence=0.60, evidence="x")) is None
    # 0.80 clears the rule bar but not the higher bar an inferred signal must meet.
    assert engine.create("c", Signal(topic="callback", confidence=0.80, evidence="x", source="llm")) is None
    assert engine.create("c", Signal(topic="callback", confidence=0.80, evidence="x")) is not None


def test_cooldown_then_repetition_limit():
    engine = NudgeEngine()
    first = engine.create("c", Signal(topic="buying_signal", confidence=0.9, evidence="x"))
    assert first is not None
    assert engine.create("c", Signal(topic="buying_signal", confidence=0.9, evidence="x")) is None
    assert engine.suppressed["c"][-1]["reason"] == "cooldown"
    # Past the cooldown it may fire again, up to the per-call repetition limit.
    engine.history["c"][0].created_at = engine.history["c"][0].created_at.replace(year=2020)
    assert engine.create("c", Signal(topic="buying_signal", confidence=0.9, evidence="x")) is not None
    for nudge in engine.history["c"]:
        nudge.created_at = nudge.created_at.replace(year=2020)
    assert len(engine.history["c"]) == MAX_PER_TOPIC
    assert engine.create("c", Signal(topic="buying_signal", confidence=0.9, evidence="x")) is None
    assert engine.suppressed["c"][-1]["reason"] == "repetition_limit"


def test_one_nudge_per_coaching_theme_but_compliance_is_exempt():
    engine = NudgeEngine()
    assert engine.create("c", Signal(topic="frustration", confidence=0.9, evidence="x")) is not None
    # Same "relationship" theme while the first is still live.
    assert engine.create("c", Signal(topic="payment_difficulty", confidence=0.9, evidence="x")) is None
    assert engine.suppressed["c"][-1]["reason"] == "topic_group_occupied"
    # Compliance findings are distinct regulatory events, so they are not collapsed.
    assert engine.create("c", Signal(topic="compliance_gap", confidence=0.95, evidence="x")) is not None
    assert engine.create("c", Signal(topic="risky_statement", confidence=0.92, evidence="x")) is not None


def test_expired_nudges_drop_out_of_the_active_list():
    engine = NudgeEngine()
    nudge = engine.create("c", Signal(topic="frustration", confidence=0.9, evidence="x"))
    assert engine.active("c") == [nudge]
    nudge.created_at = nudge.created_at.replace(year=2020)
    assert engine.active("c") == []


def test_active_list_is_ordered_by_priority_and_capped():
    engine = NudgeEngine()
    for topic in ("callback", "missed_cross_sell", "compliance_gap", "frustration"):
        engine.create("c", Signal(topic=topic, confidence=0.95, evidence="x"))
    active = engine.active("c")
    assert active[0].topic == "compliance_gap"
    assert len(active) <= 3


# --- pipeline

def test_pipeline_times_every_stage_and_reports_percentiles():
    pipeline = RealtimePipeline()
    for index, (speaker, text) in enumerate([
            ("agent", "Essential Care is $189 per month."),
            ("customer", "My wife needs cover too."),
            ("customer", "This is ridiculous, I've been waiting weeks.")]):
        result = pipeline.on_text("c", text, speaker=speaker, offset_seconds=index * 4.0)
        assert result["timing"]["total_ms"] >= 0
    report = pipeline.report("c")
    assert report["latency_ms"]["chunks"] == 3
    for stage in ("asr_ms", "signal_ms", "llm_ms", "nudge_ms", "delivery_ms", "total_ms"):
        assert {"p50", "p95", "mean", "max"} <= set(report["latency_ms"][stage])
    assert report["nudges"]["fired"] >= 2


def test_rule_only_detection_is_fast_enough_for_real_time():
    pipeline = RealtimePipeline()
    start = time.perf_counter()
    for index in range(50):
        pipeline.on_text("c", "My wife needs cover and I can't pay this month.", speaker="customer",
                         offset_seconds=index)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms / 50 < 10   # well inside a 4-second chunk budget


def test_asr_prompt_echo_is_discarded():
    """Whisper returns its own prompt on silent chunks; those words were never spoken."""
    assert echoes_prompt("Pertahankan istilah yang terbukukan hari ini.")
    assert echoes_prompt("Taglish customer call about life insurance.")
    assert not echoes_prompt("Angsuran jatuh tempo besok ya Pak.")
    assert not echoes_prompt("Nasa grace period pa po kayo.")


def test_suppressions_are_recorded_with_reasons_for_the_false_positive_analysis():
    pipeline = RealtimePipeline()
    for _ in range(4):
        pipeline.on_text("c", "This is ridiculous.", speaker="customer")
    report = pipeline.report("c")
    assert report["nudges"]["suppressed"] >= 2
    assert set(report["nudges"]["suppression_reasons"]) <= {
        "cooldown", "repetition_limit", "duplicate_message", "topic_group_occupied",
        "below_confidence_threshold"}
    assert all("evidence" in item and "reason" in item for item in report["suppressed"])
