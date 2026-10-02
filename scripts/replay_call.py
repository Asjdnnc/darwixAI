"""Q4 real-time demo: replay a recorded call at wall-clock speed and nudge while it plays.

    python scripts/replay_call.py Evidence/q3/id1.wav --market id
    python scripts/replay_call.py --scenario compliance        # scripted text scenarios

The brief rules out analysing a finished upload, so this deliberately does not read the file and
report at the end. The audio is cut into chunks and each chunk is released **only when its moment
arrives in real time** — `sleep` until the chunk's offset, then transcribe, analyse and nudge. A
90-second call takes 90 seconds, and nudges appear while there is still call left to act on.

Latency is measured from the instant a chunk is released, so queueing and ASR are both counted.
Results are written to data/q4/<name>.json.
"""
import argparse
import io
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm import GroqService  # noqa: E402
from app.nudges import NudgeEngine  # noqa: E402
from app.stream import RealtimePipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "q4"

# Scripted scenarios covering the brief's required Q4 test coverage, as (speaker, text) turns
# spaced one chunk apart. Used when no audio file is given.
SCENARIOS = {
    "compliance": [
        ("agent", "Hi, this is Sam from Darwix Health, thanks for taking my call."),
        ("customer", "Hi, I'm looking at health cover for myself."),
        ("agent", "Great. Essential Care is $189 per month for someone your age."),   # price, no disclosure
        ("customer", "That sounds reasonable. Am I definitely going to be approved?"),
        ("agent", "Yes, you're definitely approved, don't worry about the medical questions."),  # risky
        ("customer", "Okay, how do I sign up?"),
    ],
    "cross_sell": [
        ("agent", "Thanks for calling Darwix Health, how can I help?"),
        ("customer", "I need cover for myself, and my wife just lost her employer plan too."),
        ("agent", "Understood. Let me take your date of birth first."),
        ("customer", "Sure, it's the fourth of March."),
        ("agent", "Thank you. And which state do you live in?"),
    ],
    "frustration": [
        ("agent", "Thanks for holding, how can I help?"),
        ("customer", "I've already told you twice, I'm calling about my claim."),
        ("agent", "Let me pull that up for you."),
        ("customer", "This is ridiculous, I've been waiting three weeks."),
        ("agent", "I understand, let me check the status now."),
        ("customer", "I can't pay the premium this month either, I lost my job."),
    ],
    "noisy": [
        ("customer", "Sorry, can you hear me? The line is breaking up."),
        ("agent", "Yes, I can hear you now."),
        ("customer", "Hello? Hello."),
        ("agent", "Still here. Take your time."),
        ("customer", "Right, sorry, I was asking about the thing we discussed."),
        ("customer", "Never mind, it's fine, nothing urgent."),
    ],
}

CHUNK_SECONDS = 4.0


def audio_chunks(path: Path, seconds: float):
    """Split a WAV into fixed-length chunks, each tagged with its offset in the call."""
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        per_chunk = int(params.framerate * seconds)
        offset = 0.0
        while True:
            frames = source.readframes(per_chunk)
            if not frames:
                return
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as out:
                out.setparams(params)
                out.writeframes(frames)
            yield offset, buffer.getvalue()
            offset += len(frames) / (params.framerate * params.sampwidth * params.nchannels)


def show(result: dict, offset: float) -> None:
    text = result["event"]["text"].strip()
    if text:
        print(f"  [{offset:5.1f}s] {result['event']['speaker']:8} | {text[:92]}")
    for nudge in result["nudges"]:
        print(f"           \033[1m>> NUDGE ({nudge['topic']}, {nudge['confidence']:.2f}, "
              f"{result['timing']['total_ms']:.0f} ms): {nudge['message']}\033[0m")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", nargs="?", help="WAV file to replay at real-time speed")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="scripted text scenario instead")
    parser.add_argument("--market", default="en")
    parser.add_argument("--speed", type=float, default=1.0, help="1.0 is real time; higher is faster")
    parser.add_argument("--no-llm", action="store_true", help="rules only, no LLM second pass")
    args = parser.parse_args()
    if not args.audio and not args.scenario:
        parser.error("give an audio file or --scenario")

    pipeline = RealtimePipeline(
        transcriber=None if args.scenario else _make_transcriber(),
        classifier=None if args.no_llm or not settings.groq_api_key else GroqService(),
        engine=NudgeEngine())
    name = args.scenario or Path(args.audio).stem
    call_id = f"replay-{name}"
    started = time.perf_counter()
    print(f"───── replaying {name} at {args.speed}x (real time = 1.0)")

    if args.scenario:
        for index, (speaker, text) in enumerate(SCENARIOS[args.scenario]):
            offset = index * CHUNK_SECONDS
            _wait_until(started, offset / args.speed)
            show(pipeline.on_text(call_id, text, speaker=speaker, market=args.market,
                                  offset_seconds=offset), offset)
    else:
        for offset, chunk in audio_chunks(Path(args.audio), CHUNK_SECONDS):
            _wait_until(started, offset / args.speed)
            speaker = "customer" if int(offset // CHUNK_SECONDS) % 2 else "agent"
            show(pipeline.on_audio(call_id, chunk, speaker=speaker, market=args.market,
                                   offset_seconds=offset), offset)

    elapsed = time.perf_counter() - started
    report = pipeline.report(call_id)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(report, indent=2))

    latency = report["latency_ms"]
    print(f"\n  wall clock {elapsed:.1f}s for {latency['audio_seconds']}s of call "
          f"({len(SCENARIOS.get(name, [])) or latency['chunks']} chunks)")
    print(f"  end-to-end  P50 {latency['total_ms']['p50']} ms   P95 {latency['total_ms']['p95']} ms")
    for stage in ("asr_ms", "signal_ms", "llm_ms", "nudge_ms", "delivery_ms"):
        print(f"    {stage:<12} P50 {latency[stage]['p50']:>7} ms   P95 {latency[stage]['p95']:>7} ms")
    print(f"  nudges fired {report['nudges']['fired']}, suppressed {report['nudges']['suppressed']} "
          f"{report['nudges']['suppression_reasons'] or ''}")
    print(f"  wrote {(OUT / f'{name}.json').relative_to(ROOT)}")


def _wait_until(started: float, target_offset: float) -> None:
    remaining = target_offset - (time.perf_counter() - started)
    if remaining > 0:
        time.sleep(remaining)


def _make_transcriber():
    from app.main import _transcribe_chunk
    return _transcribe_chunk


if __name__ == "__main__":
    main()
