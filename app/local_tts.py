"""Local native-voice TTS for the Q3 markets, via Meta MMS-TTS (VITS) from Hugging Face.

Groq's Orpheus speaks English only and no configured cloud provider offers a Filipino voice, so
Taglish had no native voice at all. `facebook/mms-tts-tgl` and `facebook/mms-tts-ind` run locally,
need no API key and no quota, and close that gap.

Optional dependency: if torch/transformers are not installed, `available()` returns False and the
caller falls back to the browser voice. Install with:

    pip install -e '.[local-tts]'

Models are ~139 MB each, load once (about 13 s cold), then synthesize in well under a second.
Synthesized audio is cached per sentence on disk, so the fixed script lines cost nothing after the
first call.
"""
from __future__ import annotations

import hashlib
import io
import logging
import threading
import wave
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "tts_cache"

MODELS = {"ph": "facebook/mms-tts-tgl", "id": "facebook/mms-tts-ind"}

_models: dict[str, tuple] = {}
_lock = threading.Lock()


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception:
        return False
    return True


def _load(market: str):
    """Load and memoize one market's voice. Thread-safe so a warm-up thread and a request can race."""
    with _lock:
        if market not in _models:
            from transformers import AutoTokenizer, VitsModel
            repo = MODELS[market]
            log.info("loading local TTS %s", repo)
            _models[market] = (VitsModel.from_pretrained(repo), AutoTokenizer.from_pretrained(repo))
        return _models[market]


def warm(markets=("ph", "id")) -> None:
    """Load models in the background so the first call of a demo is not slow."""
    def _run():
        for market in markets:
            try:
                _load(market)
            except Exception as exc:  # never let a warm-up failure affect the server
                log.warning("local TTS warm-up failed for %s: %s", market, exc)
    threading.Thread(target=_run, daemon=True).start()


def _wav(samples, rate: int) -> bytes:
    peak = max(1e-9, float(abs(samples).max()))
    pcm = (samples / peak * 32767).astype("int16").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def synthesize(market: str, text: str, segments) -> tuple[bytes, str]:
    """Speak `text` in the market's native voice. Returns (wav bytes, "cache hits/total")."""
    import torch

    model, tokenizer = _load(market)
    rate = model.config.sampling_rate
    CACHE.mkdir(parents=True, exist_ok=True)
    parts, hits = [], 0
    pieces = segments(text)
    for piece in pieces:
        key = hashlib.sha256(f"mms|{MODELS[market]}|{piece}".encode()).hexdigest()[:24]
        path = CACHE / f"{key}.wav"
        if path.exists():
            hits += 1
        else:
            with torch.no_grad():
                waveform = model(**tokenizer(piece, return_tensors="pt")).waveform[0].numpy()
            partial = path.with_suffix(".part")
            partial.write_bytes(_wav(waveform, rate))
            partial.replace(path)
        parts.append(path.read_bytes())

    if len(parts) == 1:
        return parts[0], f"{hits}/{len(pieces)}"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        with wave.open(io.BytesIO(parts[0]), "rb") as first:
            out.setparams(first.getparams())
        for part in parts:
            with wave.open(io.BytesIO(part), "rb") as segment:
                out.writeframes(segment.readframes(segment.getnframes()))
    return buf.getvalue(), f"{hits}/{len(pieces)}"
