"""Q3 ASR check: synthesize market utterances, transcribe with Groq Whisper, measure what survives.

Run:  python scripts/asr_check.py            (all markets with a usable voice)
      python scripts/asr_check.py id
Writes data/eval/asr_results.json and prints a summary.

What this measures: whether the finance loanwords and local terms the agent depends on survive
transcription, and the word error rate against a known reference. What it does NOT measure:
real accent robustness. Synthesized speech is cleaner and more regular than a human on a mobile
line, so treat these as an upper bound and read the recorded calls in Evidence/ for real speech.
macOS ships a native Indonesian voice (Damayanti) but no Filipino voice, so the Philippine probes
can only be run from human recordings.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from groq import Groq  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import ASR_LANGUAGE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "eval" / "asr_utterances.json"
RESULTS = ROOT / "data" / "eval" / "asr_results.json"

PROMPTS = {
    "tl": ("Taglish customer call about life insurance. Keep English terms: premium, policy, coverage, "
           "rider, lapse, grace period, due date, beneficiary, GCash, Bayad Center."),
    "id": ("Percakapan nasabah pembiayaan. Pertahankan istilah: angsuran, cicilan, tenor, denda, "
           "jatuh tempo, DP, pembiayaan, virtual account, Indomaret, Alfamart, GoPay, OVO."),
}


def normalize(text: str) -> list[str]:
    return [w.strip(".,!?;:\"'").lower() for w in text.split() if w.strip(".,!?;:\"'")]


def wer(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, divided by reference length."""
    r, h = normalize(reference), normalize(hypothesis)
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(prev[j - 1] if rw == hw else 1 + min(prev[j - 1], prev[j], cur[j - 1]))
        prev = cur
    return round(prev[-1] / max(1, len(r)), 3)


def available(voice: str | None) -> bool:
    if not voice:
        return False
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    return any(line.startswith(voice) for line in listing.splitlines())


def synthesize(text: str, voice: str, path: Path) -> None:
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(path)], check=True)


def main(markets: list[str]) -> None:
    fixture = json.loads(FIXTURE.read_text())
    client = Groq(api_key=settings.groq_api_key, max_retries=2)
    report = {}
    with tempfile.TemporaryDirectory() as tmp:
        for market in markets or [k for k in fixture if not k.startswith("_")]:
            spec = fixture[market]
            voice, language = spec["voice"], spec["language"]
            if not available(voice):
                report[market] = {"status": "skipped", "language": language, "voice": voice,
                                  "reason": f"no {('Filipino' if market == 'ph' else 'local')} system voice is "
                                            f"installed, so this market can only be measured from human recordings"}
                print(f"[{market}] skipped: no usable voice; use the recorded calls instead")
                continue
            rows = []
            for utt in spec["utterances"]:
                wav = Path(tmp) / f"{utt['id']}.wav"
                synthesize(utt["text"], voice, wav)
                with wav.open("rb") as fh:
                    result = client.audio.transcriptions.create(
                        file=(wav.name, fh.read()), model=settings.groq_stt_model,
                        language=language, prompt=PROMPTS.get(language, ""), response_format="json")
                heard = result.text.strip()
                kept = [t for t in utt["must_keep"] if t.lower() in heard.lower()]
                lost = [t for t in utt["must_keep"] if t.lower() not in heard.lower()]
                rows.append({**utt, "heard": heard, "wer": wer(utt["text"], heard),
                             "terms_kept": kept, "terms_lost": lost})
                print(f"  {utt['id']} wer={rows[-1]['wer']:<6} lost={lost or '-'}")
            total_terms = sum(len(r["must_keep"]) for r in rows)
            report[market] = {
                "status": "measured", "language": language, "voice": voice,
                "model": settings.groq_stt_model,
                "utterances": len(rows),
                "mean_wer": round(sum(r["wer"] for r in rows) / len(rows), 3),
                "term_retention": f"{total_terms - sum(len(r['terms_lost']) for r in rows)}/{total_terms}",
                "rows": rows,
            }
            print(f"[{market}] mean WER {report[market]['mean_wer']} | "
                  f"terms kept {report[market]['term_retention']}")
    RESULTS.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {RESULTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main([m for m in sys.argv[1:] if m in {"ph", "id"}])
