"""Q4 real-time pipeline: audio chunk in, nudges out, every stage timed.

The brief is explicit that analysing a finished recording does not qualify, so the unit of work here
is a single chunk of a call that is still in progress. One chunk moves through:

    received -> ASR -> signal extraction -> nudge generation -> delivered

Each boundary is timestamped, so the latency report is measured rather than estimated, and each
stage can be attributed separately. `t_received` is set the moment the bytes arrive, so queueing
time is counted against us rather than hidden.

Delivery is over a WebSocket when the dashboard is attached, and the same `Nudge` objects are
readable from the polling endpoint, so the transport is not what makes it real time.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

import re

from app.models import Nudge, Signal, TranscriptEvent
from app.nudges import NudgeEngine
from app.signals import CallState, detect_rules, detect_llm


# The instruction half of each ASR prompt. Domain terms ("jatuh tempo", "grace period") are
# deliberately excluded: those are legitimate speech, while this phrasing never is.
PROMPT_ECHO_RE = re.compile(
    r"taglish customer call|keep english terms|spell (?:these|payment channels)|"
    r"percakapan nasabah pembiayaan|pertahankan istilah|customer call about life insurance",
    re.I)


def echoes_prompt(text: str, _prompt: str = "") -> bool:
    """Whisper emits its own prompt back when a chunk is silence or unintelligible.

    Observed replaying real call audio: near-silent chunks came back as "Pertahankan istilah yang
    terbukukan hari ini", lifted from the ASR prompt. Treating that as speech produces nudges from
    words nobody said, so it is discarded.
    """
    return bool(PROMPT_ECHO_RE.search(text))


@dataclass
class ChunkTiming:
    """Milliseconds spent in each stage of one chunk."""
    offset_seconds: float
    asr_ms: float = 0.0
    signal_ms: float = 0.0
    llm_ms: float = 0.0
    nudge_ms: float = 0.0
    delivery_ms: float = 0.0
    total_ms: float = 0.0
    words: int = 0
    nudges: int = 0

    def as_dict(self) -> dict:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class StreamSession:
    call_id: str
    market: str = "en"
    state: CallState = field(default_factory=CallState)
    timings: list[ChunkTiming] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    prompt_echoes: int = 0   # chunks where ASR returned its own prompt instead of speech
    started: float = field(default_factory=time.perf_counter)

    def latency_report(self) -> dict:
        """P50/P95 overall and per stage. Returned live, so it can be shown during the call."""
        if not self.timings:
            return {"chunks": 0}

        def pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            index = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
            return round(ordered[index], 1)

        stages = ["asr_ms", "signal_ms", "llm_ms", "nudge_ms", "delivery_ms", "total_ms"]
        report = {"chunks": len(self.timings),
                  "nudges": sum(t.nudges for t in self.timings),
                  "audio_seconds": round(max((t.offset_seconds for t in self.timings), default=0), 1)}
        for stage in stages:
            values = [getattr(t, stage) for t in self.timings]
            report[stage] = {"p50": pct(values, 50), "p95": pct(values, 95),
                             "mean": round(statistics.fmean(values), 1), "max": round(max(values), 1)}
        return report


class RealtimePipeline:
    """Holds every in-flight call. One instance serves the API and the replay harness alike."""

    def __init__(self, transcriber=None, classifier=None, engine: NudgeEngine | None = None) -> None:
        self.transcriber = transcriber        # callable(audio_bytes, market) -> text
        self.classifier = classifier          # GroqService or None to run rules only
        self.engine = engine or NudgeEngine()
        self.sessions: dict[str, StreamSession] = {}

    @staticmethod
    def asr_prompt(market: str) -> str:
        from app.main import ASR_LANGUAGE, ASR_PROMPTS
        return ASR_PROMPTS.get(ASR_LANGUAGE.get(market, "en"), "")

    def session(self, call_id: str, market: str = "en") -> StreamSession:
        if call_id not in self.sessions:
            self.sessions[call_id] = StreamSession(call_id=call_id, market=market)
        return self.sessions[call_id]

    # ------------------------------------------------------------------ one chunk

    def on_audio(self, call_id: str, audio: bytes, speaker: str = "unknown", market: str = "en",
                 offset_seconds: float = 0.0, t_received: float | None = None) -> dict:
        """Audio chunk from a live call. ASR runs here, then the text path."""
        t_received = t_received or time.perf_counter()
        session = self.session(call_id, market)
        t0 = time.perf_counter()
        text = self.transcriber(audio, market) if self.transcriber else ""
        asr_ms = (time.perf_counter() - t0) * 1000
        if echoes_prompt(text, self.asr_prompt(market)):
            session.prompt_echoes += 1
            text = ""
        return self.on_text(call_id, text, speaker=speaker, market=market, offset_seconds=offset_seconds,
                            t_received=t_received, asr_ms=asr_ms)

    def on_text(self, call_id: str, text: str, speaker: str = "unknown", market: str = "en",
                offset_seconds: float = 0.0, t_received: float | None = None,
                asr_ms: float = 0.0) -> dict:
        """Transcript chunk from a live call (already transcribed, or typed in the UI)."""
        t_received = t_received or time.perf_counter()
        session = self.session(call_id, market)
        timing = ChunkTiming(offset_seconds=offset_seconds, asr_ms=asr_ms, words=len(text.split()))

        t0 = time.perf_counter()
        signals: list[Signal] = detect_rules(text, speaker, session.state) if text.strip() else []
        timing.signal_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        if self.classifier is not None and text.strip():
            signals = signals + detect_llm(self.classifier, text, speaker, signals)
        timing.llm_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        nudges: list[Nudge] = [n for s in signals if (n := self.engine.create(call_id, s))]
        timing.nudge_ms = (time.perf_counter() - t0) * 1000
        timing.nudges = len(nudges)

        event = TranscriptEvent(call_id=call_id, speaker=speaker, market=market, text=text,
                                offset_seconds=offset_seconds)
        session.transcript.append({"speaker": speaker, "text": text, "offset_seconds": offset_seconds})

        timing.delivery_ms = (time.perf_counter() - t_received) * 1000 - (
            timing.asr_ms + timing.signal_ms + timing.llm_ms + timing.nudge_ms)
        timing.total_ms = (time.perf_counter() - t_received) * 1000
        session.timings.append(timing)

        return {"event": event.model_dump(mode="json"),
                "signals": [s.model_dump() for s in signals],
                "nudges": [n.model_dump(mode="json") for n in nudges],
                "active": [n.model_dump(mode="json") for n in self.engine.active(call_id)],
                "timing": timing.as_dict()}

    # ------------------------------------------------------------------ reporting

    def report(self, call_id: str) -> dict:
        session = self.sessions.get(call_id)
        if not session:
            return {"call_id": call_id, "status": "unknown call"}
        return {"call_id": call_id, "market": session.market,
                "asr_prompt_echoes_discarded": session.prompt_echoes,
                "latency_ms": session.latency_report(),
                "nudges": self.engine.summary(call_id),
                "suppressed": self.engine.suppressed.get(call_id, []),
                "transcript": session.transcript}
