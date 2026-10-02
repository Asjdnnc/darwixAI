"""Run a whole call end to end as audio and save it: synthesized caller -> real ASR -> agent -> TTS.

    python scripts/simulate_call.py ph1

This is NOT a recording of a human caller. The caller's lines are synthesized with the same local
MMS voice the agent uses, so the result is a *simulated* call. What makes it useful as evidence is
that nothing is shortcut: each caller line is turned into audio, transcribed by the real Groq
Whisper endpoint (so genuine ASR errors appear), fed to the real agent, and the reply is synthesized
back. Output goes to data/simulated_calls/, which is deliberately not the Evidence/ folder.

Human-recorded calls remain the stronger evidence for the assessment; this exists so the pipeline
can be demonstrated and re-run deterministically.
"""
import io
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from groq import Groq  # noqa: E402

from app import local_tts  # noqa: E402
from app.config import settings  # noqa: E402
from app.crm import MockCRM  # noqa: E402
from app.ingest import load_records  # noqa: E402
from app.kb import KnowledgeBase  # noqa: E402
from app.llm import GroqService  # noqa: E402
from app.main import ASR_LANGUAGE, ASR_PROMPTS, tts_segments  # noqa: E402
from app.reminder_agent import ReminderAgent  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "simulated_calls"

CALLS = {
    "ph1": ("ph", ["Opo, sige po.",
                   "Ano po ang mangyayari kung hindi ako makabayad?",
                   "Pwede po ba sa GCash?",
                   "Sa kinsenas po."]),
    "ph2": ("ph", ["Opo.",
                   "Mahal po masyado ang premium, pwede po bang gawing quarterly?",
                   "Wala po akong pera ngayon."]),
    "id1": ("id", ["Iya, boleh.",
                   "Dendanya berapa kalau telat?",
                   "Bisa lewat Indomaret?",
                   "Besok."]),
    "id2": ("id", ["Iya, boleh.",
                   "Nggih Mbak, dendanya kok mahal ya?",
                   "Udah lah, mau bicara dengan petugas aja."]),
}

SILENCE_SECONDS = 0.4


def speak(market: str, text: str) -> bytes:
    audio, _ = local_tts.synthesize(market, text, tts_segments)
    return audio


def transcribe(audio: bytes, market: str) -> str:
    language = ASR_LANGUAGE[market]
    result = Groq(api_key=settings.groq_api_key, max_retries=2).audio.transcriptions.create(
        file=("turn.wav", audio), model=settings.groq_stt_model, language=language,
        prompt=ASR_PROMPTS.get(language, ""), response_format="json")
    return result.text.strip()


def pcm_of(wav_bytes: bytes) -> tuple[bytes, tuple]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.readframes(w.getnframes()), w.getparams()


def main(name: str) -> None:
    market, lines = CALLS[name]
    kbs = {}
    for m in ("ph", "id"):
        kb = KnowledgeBase(market=m)
        for record in load_records(market=m):
            kb.upsert(record)
        kbs[m] = kb
    agent = ReminderAgent(kbs, GroqService(), MockCRM(), None)

    OUT.mkdir(parents=True, exist_ok=True)
    track, params, transcript = [], None, []

    def add(role: str, text: str, audio: bytes, **extra) -> None:
        nonlocal params
        pcm, p = pcm_of(audio)
        params = params or p
        track.append(pcm)
        track.append(b"\x00" * int(p.framerate * p.sampwidth * SILENCE_SECONDS))
        transcript.append({"role": role, "text": text, **extra})
        print(f"  {role:8} | {text[:120]}")

    opening = agent.start(name, market)
    add("agent", opening["text"], speak(market, opening["text"]))

    for line in lines:
        said = speak(market, line)
        heard = transcribe(said, market)
        add("customer", heard, said, spoken=line, asr_changed=heard.lower() != line.lower())
        reply = agent.turn(name, heard, market)
        if reply["text"].strip():
            add("agent", reply["text"], speak(market, reply["text"]),
                citations=reply["citations"], outcome=reply["outcome"]["status"])
        if reply["end_call"]:
            break

    session = agent.sessions[name]
    wav_path, json_path = OUT / f"{name}.wav", OUT / f"{name}.json"
    with wave.open(str(wav_path), "wb") as out:
        out.setparams(params)
        out.writeframes(b"".join(track))
    json_path.write_text(json.dumps({
        "call": name, "market": market, "synthesized": True,
        "note": ("Caller audio is synthesized with facebook/mms-tts-%s; transcription, agent and "
                 "replies are the real pipeline. Not a human-recorded call."
                 % ("tgl" if market == "ph" else "ind")),
        "outcome": session.outcome, "captured": session.lead,
        "actions": [a["type"] for a in session.actions],
        "guards": sorted({g["guard"] for g in session.guard_events}),
        "transcript": transcript,
    }, indent=2, ensure_ascii=False))

    duration = sum(len(t) for t in track) / (params.framerate * params.sampwidth)
    print(f"\n  outcome: {session.outcome['status']} | captured: {session.lead}")
    print(f"  {wav_path.relative_to(ROOT)} ({duration:.0f}s)  +  {json_path.relative_to(ROOT)}")


if __name__ == "__main__":
    for arg in sys.argv[1:] or list(CALLS):
        print(f"───── {arg}")
        main(arg)
