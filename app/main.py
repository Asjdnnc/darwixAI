import hashlib
import io
import logging
import re
import wave
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from groq import Groq, RateLimitError
from uuid import uuid4

from app.agent import LeadQualificationAgent
from app.config import settings
from app.crm import MockCRM
from app.kb import KnowledgeBase
from app.llm import GroqService
from app.models import AgentTurn, TranscriptEvent
from app import local_tts
from app.flows import FLOWS, flow
from app.ingest import load_records
from app.nudges import NudgeEngine
from app.reminder_agent import ReminderAgent
from app.stream import RealtimePipeline
from app.seed import seed

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="AI Engineer Assessment API", version="0.3.0")
kb, groq, nudge_engine = KnowledgeBase(), GroqService(), NudgeEngine()
seed(kb)
crm = MockCRM()
agent = LeadQualificationAgent(kb, groq, crm)

# Q3: one knowledge base per market, each with its own language settings.
market_kbs: dict[str, KnowledgeBase] = {}
for _market in FLOWS:
    _kb = KnowledgeBase(market=_market)
    for _record in load_records(market=_market):
        _kb.upsert(_record)
    market_kbs[_market] = _kb
reminder_agent = ReminderAgent(market_kbs, groq, crm)

if local_tts.available():  # load the native voices in the background so the first call is not slow
    local_tts.warm()


def _transcribe_chunk(audio: bytes, market: str) -> str:
    """ASR for a live Q4 chunk, using the same model and prompts as the voice agent."""
    language = ASR_LANGUAGE.get(market, "en")
    result = Groq(api_key=settings.groq_api_key, max_retries=0).audio.transcriptions.create(
        file=("chunk.wav", audio), model=settings.groq_stt_model, language=language,
        prompt=ASR_PROMPTS.get(language, ""), response_format="json")
    return result.text.strip()


# Q4: one pipeline instance serves the API, the WebSocket dashboard and the replay harness.
pipeline = RealtimePipeline(transcriber=_transcribe_chunk, classifier=groq, engine=nudge_engine)
# call_id -> dashboards currently watching it
watchers: dict[str, list[WebSocket]] = {}


async def _broadcast(call_id: str, payload: dict) -> None:
    for socket in list(watchers.get(call_id, [])):
        try:
            await socket.send_json(payload)
        except Exception:
            watchers.get(call_id, []).remove(socket)

# Whisper language codes per market; Filipino is "tl".
ASR_LANGUAGE = {"en": "en", "ph": "tl", "id": "id"}

# A language-specific prompt measurably improves code-switched audio: it primes Whisper to keep
# English finance loanwords in English and to spell local terms the way the extractors expect.
ASR_PROMPTS = {
    "tl": ("Taglish customer call about life insurance. Keep English terms in English: premium, "
           "policy, coverage, rider, lapse, grace period, due date, beneficiary, reinstate, "
           "quarterly, advisor. Spell these exactly: GCash, Maya, Bayad Center, 7-Eleven, "
           "kinsenas, katapusan, sahod, sweldo."),
    "id": ("Percakapan nasabah pembiayaan. Pertahankan istilah: angsuran, cicilan, tenor, denda, "
           "jatuh tempo, DP, pembiayaan, virtual account, Indomaret, Alfamart, GoPay, OVO."),
}


def agent_for(market: str):
    """Q1 lead qualification for en; the Q3 reminder flows for ph and id."""
    return reminder_agent if market in FLOWS else agent
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/kb-sources", StaticFiles(directory="data/raw"), name="kb-sources")
app.mount("/kb-docs", StaticFiles(directory="docs"), name="kb-docs")


@app.get("/")
def browser_voice_ui():
    """A minimal browser UI replaces the former telephony-provider dependency."""
    return FileResponse("app/static/index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "knowledge_chunks": len(kb.chunks),
            "markets": {m: {"records": len(k.records), "chunks": len(k.chunks), "flow": flow(m).name,
                            "sector": flow(m).sector} for m, k in market_kbs.items()}}


@app.get("/markets")
def markets() -> dict:
    """What each market bot is configured to do, for the UI and the demo."""
    return {"en": {"sector": "US health insurance", "flow": "lead_qualification", "language": "English",
                   "asr": ASR_LANGUAGE["en"], "records": len(kb.records)},
            **{m: {"sector": flow(m).sector, "flow": flow(m).name, "language": flow(m).language,
                   "asr": ASR_LANGUAGE[m], "records": len(market_kbs[m].records)} for m in FLOWS}}


@app.get("/retrieve")
def retrieve(q: str, limit: int = 3, grounded_only: bool = False, category: str | None = None,
             market: str = "en"):
    base = market_kbs.get(market, kb)
    return base.retrieve(q, limit=limit, grounded_only=grounded_only, category=category)


@app.post("/calls/start")
def start_call(market: str = "en"):
    """Open a call session; returns the greeting with the recording disclosure."""
    return agent_for(market).start(str(uuid4()), market)


@app.post("/agent/turn")
def agent_turn(turn: AgentTurn):
    """One caller utterance -> grounded, guarded reply plus the updated lead state."""
    return agent_for(turn.market).turn(turn.call_id, turn.customer_text, turn.market)


@app.get("/calls/{call_id}")
def get_call(call_id: str):
    for handler in (agent, reminder_agent):
        if call_id in handler.sessions:
            return handler.sessions[call_id].snapshot()
    raise HTTPException(status_code=404, detail="Unknown call")


@app.post("/calls/{call_id}/end")
def end_call(call_id: str):
    handler = reminder_agent if call_id in reminder_agent.sessions else agent
    return handler.end(call_id)


@app.get("/crm/{kind}")
def crm_records(kind: str):
    if kind not in {"leads", "callbacks", "escalations", "promises", "notes"}:
        raise HTTPException(status_code=404, detail="Use leads, callbacks, escalations, promises or notes")
    return crm.list(kind)


@app.post("/calls/transcript")
async def transcript(event: TranscriptEvent):
    """One transcript chunk of a call that is still in progress; never a finished upload."""
    result = pipeline.on_text(event.call_id, event.text, speaker=event.speaker, market=event.market,
                              offset_seconds=event.offset_seconds or 0.0)
    await _broadcast(event.call_id, result)
    return result


@app.post("/calls/{call_id}/audio")
async def transcript_audio(call_id: str, request: Request, speaker: str = "unknown",
                           market: str = "en", offset_seconds: float = 0.0):
    """One chunk of live call audio: transcribed, analysed and nudged in a single pass."""
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="Audio body is required")
    if not settings.groq_api_key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY is required for transcription")
    try:
        result = pipeline.on_audio(call_id, audio, speaker=speaker, market=market,
                                   offset_seconds=offset_seconds)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"chunk transcription failed: {exc}") from exc
    await _broadcast(call_id, result)
    return result


@app.get("/calls/{call_id}/nudges")
def live_nudges(call_id: str):
    """Polling alternative to the WebSocket: what the agent should see right now."""
    return {"call_id": call_id,
            "active": [n.model_dump(mode="json") for n in nudge_engine.active(call_id)],
            "summary": nudge_engine.summary(call_id)}


@app.get("/calls/{call_id}/report")
def live_report(call_id: str):
    """Measured latency, nudge counts and every suppression with its reason."""
    return pipeline.report(call_id)


@app.websocket("/ws/nudges/{call_id}")
async def nudge_socket(websocket: WebSocket, call_id: str):
    """Live nudge feed for the agent dashboard."""
    await websocket.accept()
    watchers.setdefault(call_id, []).append(websocket)
    await websocket.send_json({"active": [n.model_dump(mode="json") for n in nudge_engine.active(call_id)]})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in watchers.get(call_id, []):
            watchers[call_id].remove(websocket)


@app.post("/voice/transcribe")
async def transcribe_audio(request: Request, language: str | None = None):
    """Browser microphone upload -> Groq Whisper transcription."""
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="Audio body is required")
    try:
        if not settings.groq_api_key:
            raise HTTPException(status_code=503, detail="GROQ_API_KEY is required for transcription")
        client = Groq(api_key=settings.groq_api_key)
        result = client.audio.transcriptions.create(
            file=("recording.webm", audio), model=settings.groq_stt_model,
            language=language, prompt=ASR_PROMPTS.get(language, ""), response_format="json",
        )
        return {"text": result.text, "language": language}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Groq transcription failed") from exc


@app.post("/voice/speak")
def speak_text(turn: AgentTurn):
    # Synthesize the text provided directly instead of recalculating the agent turn.
    text_to_speak = turn.customer_text
    try:
        if turn.market in {"ph", "id"}:
            # Native local voice (Meta MMS-TTS) is preferred: it is the only Filipino voice
            # available anywhere in this stack, needs no key and no quota.
            if local_tts.available():
                try:
                    audio, cached = local_tts.synthesize(turn.market, text_to_speak, tts_segments)
                    return Response(content=audio, media_type="audio/wav", headers={
                        "X-TTS-Engine": f"mms-tts:{'tgl' if turn.market == 'ph' else 'ind'}",
                        "X-TTS-Cache": cached,
                        "Access-Control-Expose-Headers": "X-TTS-Engine, X-TTS-Cache"})
                except Exception as exc:
                    log.warning("local TTS failed for %s: %s", turn.market, exc)
            # Groq Orpheus speaks English only, so a Taglish or Bahasa line sent to it would come
            # back as English-accented nonsense; the browser speaks instead.
            # See "Native TTS" in docs/Q3_MARKETS.md.
            language = "Filipino" if turn.market == "ph" else "Indonesian"
            raise HTTPException(
                status_code=501,
                detail=(f"No native {language} voice in this process. Install the local voices with "
                        "`pip install -e '.[local-tts]'` and run the server from that same "
                        "environment (e.g. .venv/bin/uvicorn app.main:app); using the browser voice."))
        if not settings.groq_api_key:
            raise HTTPException(status_code=503, detail="GROQ_API_KEY is required for English speech synthesis")
        try:
            audio, cached = synthesize("groq", text_to_speak)
            engine = f"groq-orpheus:{settings.groq_tts_voice}"
        except RateLimitError:
            # Orpheus has a small daily quota. Keep one consistent voice for the rest of the
            # call by switching to OpenAI rather than dropping to the browser's system voice.
            if not settings.openai_api_key:
                raise
            try:
                audio, cached = synthesize("openai", text_to_speak)
                engine = f"openai:{settings.openai_tts_voice} (groq quota reached)"
            except Exception as exc:  # no OpenAI credit either: let the browser speak
                raise HTTPException(status_code=429,
                                    detail=f"Groq TTS quota reached; OpenAI fallback unavailable ({type(exc).__name__})") from exc
        return Response(content=audio, media_type="audio/wav", headers={
            "X-TTS-Engine": engine, "X-TTS-Cache": cached,
            "Access-Control-Expose-Headers": "X-TTS-Engine, X-TTS-Cache"})
    except HTTPException:
        raise
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail="Text-to-speech daily quota reached") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq speech synthesis failed: {exc}") from exc


TTS_CACHE = Path(__file__).resolve().parent.parent / "data" / "tts_cache"


def tts_segments(text: str, limit: int = 190) -> list[str]:
    """Sentence-sized segments (Orpheus works best on short inputs; sentences are cacheable)."""
    segments = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            segments.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if sentence:
            segments.append(sentence)
    return segments or [text]


def read_wav(data: bytes) -> tuple[tuple[int, int, int], bytes]:
    """Return ((channels, rate, sample_width), pcm) from a WAV file.

    Groq streams WAV with placeholder sizes (0xFFFFFFFF) in the RIFF and data headers, which the
    stdlib wave module rejects, so chunks are walked manually and a placeholder means "to end of file".
    """
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        chunk, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if chunk == b"fmt ":
            channels = int.from_bytes(data[body + 2:body + 4], "little")
            rate = int.from_bytes(data[body + 4:body + 8], "little")
            bits = int.from_bytes(data[body + 14:body + 16], "little")
            fmt = (channels, rate, bits // 8)
        elif chunk == b"data":
            end = len(data) if size == 0xFFFFFFFF or body + size > len(data) else body + size
            if fmt is None:
                raise ValueError("WAV data before fmt chunk")
            return fmt, data[body:end]
        pos = body + size + (size & 1)
    raise ValueError("WAV has no data chunk")


def speak_segment(engine: str, segment: str, path: Path) -> None:
    if engine == "groq":
        audio = Groq(api_key=settings.groq_api_key, max_retries=1).audio.speech.create(
            model=settings.groq_tts_model, voice=settings.groq_tts_voice, input=segment, response_format="wav")
        partial = path.with_suffix(".part")
        audio.write_to_file(partial)  # groq>=1.x returns a BinaryAPIResponse, which has no .content
        partial.replace(path)
        return
    from openai import OpenAI
    response = OpenAI(api_key=settings.openai_api_key).audio.speech.create(
        model=settings.openai_tts_model, voice=settings.openai_tts_voice, input=segment, response_format="wav",
        instructions="You are a calm, friendly US health insurance advisor on a phone call. Speak naturally.")
    partial = path.with_suffix(".part")
    response.write_to_file(partial)
    partial.replace(path)


def synthesize(engine: str, text: str) -> tuple[bytes, str]:
    """Text to speech with a per-sentence disk cache.

    Fixed phrases (greeting, qualification questions, escalation script) are synthesized once and
    reused, which stretches the small daily TTS quota across many more calls.
    """
    voice = settings.groq_tts_voice if engine == "groq" else settings.openai_tts_voice
    model = settings.groq_tts_model if engine == "groq" else settings.openai_tts_model
    TTS_CACHE.mkdir(parents=True, exist_ok=True)
    parts, hits = [], 0
    segments = tts_segments(text)
    for segment in segments:
        key = hashlib.sha256(f"{model}|{voice}|{segment}".encode()).hexdigest()[:24]
        path = TTS_CACHE / f"{key}.wav"
        if path.exists():
            hits += 1
        else:
            speak_segment(engine, segment, path)
        parts.append(path.read_bytes())
    fmt, pcm = None, []
    for part in parts:
        part_fmt, frames = read_wav(part)
        fmt = fmt or part_fmt
        pcm.append(frames)
    channels, rate, width = fmt
    output = io.BytesIO()
    with wave.open(output, "wb") as w_out:
        w_out.setnchannels(channels)
        w_out.setframerate(rate)
        w_out.setsampwidth(width)
        w_out.writeframes(b"".join(pcm))
    return output.getvalue(), f"{hits}/{len(segments)}"


@app.post("/calls/save_log")
def save_call_log(log_data: dict):
    import os, time, json
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_logs")
    os.makedirs(log_dir, exist_ok=True)
    filename = f"call_log_{int(time.time())}.json"
    filepath = os.path.join(log_dir, filename)
    with open(filepath, "w") as f:
        json.dump(log_data, f, indent=2)
    return {"status": "success", "file": filename}
