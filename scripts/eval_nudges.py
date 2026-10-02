"""Q4 false-positive analysis: run labelled chunks through the real pipeline and score it.

    python scripts/eval_nudges.py              # rules only (deterministic, the default)
    python scripts/eval_nudges.py --llm        # include the LLM second pass

Writes data/eval/q4_results.json. Positives expect a specific topic; negatives are adversarial —
they carry a signal's vocabulary without its substance ("the weather has been terrible", "I was
reading about your family plan") and must produce no nudge at all.

Each case runs in its own call so cooldowns and grouping do not mask a detection failure; the
suppression controls are measured separately in the replay scenarios.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm import GroqService  # noqa: E402
from app.nudges import NudgeEngine  # noqa: E402
from app.stream import RealtimePipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "eval" / "q4_chunks.json"
RESULTS = ROOT / "data" / "eval" / "q4_results.json"


def main(use_llm: bool) -> None:
    cases = json.loads(FIXTURE.read_text())["cases"]
    classifier = GroqService() if use_llm and settings.groq_api_key else None
    rows, tp, fp, fn, tn = [], 0, 0, 0, 0

    for case in cases:
        pipeline = RealtimePipeline(classifier=classifier, engine=NudgeEngine())
        result = pipeline.on_text(case["id"], case["text"], speaker=case["speaker"])
        fired = [n["topic"] for n in result["nudges"]]
        expected = case["expect"]

        if expected is None:
            outcome = "true_negative" if not fired else "FALSE POSITIVE"
            tn += not fired
            fp += bool(fired)
        elif expected in fired:
            outcome = "true_positive"
            tp += 1
        else:
            outcome = "FALSE NEGATIVE"
            fn += 1
        rows.append({**case, "fired": fired, "outcome": outcome,
                     "signals": [{"topic": s["topic"], "confidence": s["confidence"], "source": s["source"]}
                                 for s in result["signals"]]})
        flag = "" if outcome.islower() else f"   <-- {outcome}"
        print(f"  {case['id']}  expect={str(expected):<18} fired={fired or '[]'}{flag}")

    positives = tp + fn
    negatives = tn + fp
    summary = {
        "mode": "rules+llm" if classifier else "rules only",
        "cases": len(cases), "positives": positives, "negatives": negatives,
        "true_positives": tp, "false_negatives": fn, "true_negatives": tn, "false_positives": fp,
        "recall": round(tp / positives, 3) if positives else None,
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "false_positive_rate": round(fp / negatives, 3) if negatives else None,
    }
    RESULTS.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(f"\n  {summary['mode']}: recall {summary['recall']}, precision {summary['precision']}, "
          f"false-positive rate {summary['false_positive_rate']}")
    print(f"  TP {tp}  FN {fn}  TN {tn}  FP {fp}")
    print(f"  wrote {RESULTS.relative_to(ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="include the LLM second pass")
    main(parser.parse_args().llm)
