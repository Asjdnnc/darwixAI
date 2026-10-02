"""Q2 retrieval evaluation: gold queries -> ranked records, verdicts, metrics, threshold sweep.

Run:  python scripts/eval_retrieval.py
Writes docs/RETRIEVAL_EVAL.md and data/eval/retrieval_results.json.

Verdicts use the *grounded* results (what the voice agent actually receives):
  correct            in-scope: an expected record is ranked first; out-of-scope: nothing passes the gates
  partially correct  in-scope: an expected record is in the top 3 but not first
  incorrect          otherwise (wrong records, or nothing retrieved for an answerable question)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import kb as kb_module  # noqa: E402
from app.kb import KnowledgeBase  # noqa: E402
from app.seed import seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "data" / "eval" / "retrieval_queries.json"


def verdict(expected: list[str], refs: list[str]) -> str:
    if not expected:
        return "correct" if not refs else "incorrect"
    if refs and refs[0] in expected:
        return "correct"
    return "partially correct" if any(r in expected for r in refs) else "incorrect"


def evaluate(kb: KnowledgeBase, queries: list[dict], min_score: float, min_coverage: float) -> list[dict]:
    rows = []
    for q in queries:
        ranked = [i for i in kb.score(q["query"])]
        if ranked:
            ranked = [i for i in ranked if i["score"] >= ranked[0]["score"] * kb_module.RELATIVE_CUTOFF]
        grounded = [i for i in ranked if i["score"] >= min_score and i["coverage"] >= min_coverage][:3]
        refs = [i["chunk"].source_ref for i in grounded]
        rank = next((n for n, r in enumerate(refs, 1) if r in q["expected"]), None)
        rows.append({**q, "results": [{"record_id": i["chunk"].record_id, "title": i["chunk"].title,
                                       "source_ref": i["chunk"].source_ref, "score": i["score"],
                                       "coverage": i["coverage"], "matched": i["matched"],
                                       "content": i["chunk"].content} for i in grounded],
                     "best_ungated": ({"source_ref": ranked[0]["chunk"].source_ref, "score": ranked[0]["score"],
                                       "coverage": ranked[0]["coverage"]} if ranked else None),
                     "rank": rank, "verdict": verdict(q["expected"], refs)})
    return rows


def metrics(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["expected"]]
    oos = [r for r in rows if not r["expected"]]
    return {
        "queries": len(rows),
        "correct": sum(r["verdict"] == "correct" for r in rows),
        "partially_correct": sum(r["verdict"] == "partially correct" for r in rows),
        "incorrect": sum(r["verdict"] == "incorrect" for r in rows),
        "top1_accuracy": round(sum(r["rank"] == 1 for r in answerable) / len(answerable), 3),
        "recall_at_3": round(sum(r["rank"] is not None for r in answerable) / len(answerable), 3),
        "mrr": round(sum(1 / r["rank"] for r in answerable if r["rank"]) / len(answerable), 3),
        "out_of_scope_rejected": f"{sum(not r['results'] for r in oos)}/{len(oos)}",
    }


def explanation(row: dict) -> str:
    if not row["results"]:
        best = row["best_ungated"]
        if not best:
            return "No knowledge-base term matched the question."
        return (f"Rejected by the grounding gates: best candidate `{best['source_ref']}` scored "
                f"{best['score']} with coverage {best['coverage']} (needs ≥{kb_module.MIN_SCORE} and "
                f"≥{kb_module.MIN_COVERAGE}). " + ("The agent will say the information is unavailable."
                                                   if not row["expected"] else ""))
    top = row["results"][0]
    text = f"{row['why']} Matched terms: {', '.join(top['matched'])}; score {top['score']}, coverage {top['coverage']}."
    if row["verdict"] == "partially correct":
        text += f" An expected record is at rank {row['rank']}, not rank 1."
    if row["verdict"] == "incorrect" and row["expected"]:
        text += " No expected record was retrieved."
    if row["verdict"] == "incorrect" and not row["expected"]:
        text += " Out-of-scope question passed the gates (false positive)."
    return text


def main() -> None:
    queries = json.loads(QUERIES.read_text())["queries"]
    kb = KnowledgeBase()
    seed(kb)
    rows = evaluate(kb, queries, kb_module.MIN_SCORE, kb_module.MIN_COVERAGE)
    splits = {name: metrics([r for r in rows if r["split"] == name]) for name in ("tuning", "held_out")}
    summary = {**metrics(rows), "by_split": splits}

    sweep = []
    for min_score in (1.5, 2.0, 2.5, 3.0, 3.5):
        for min_cov in (0.3, 0.35, 0.4, 0.45, 0.5):
            m = metrics(evaluate(kb, queries, min_score, min_cov))
            sweep.append({"min_score": min_score, "min_coverage": min_cov, **m})

    (ROOT / "data" / "eval" / "retrieval_results.json").write_text(
        json.dumps({"config": {"min_score": kb_module.MIN_SCORE, "min_coverage": kb_module.MIN_COVERAGE,
                               "title_weight": kb_module.TITLE_WEIGHT, "k1": kb_module.K1, "b": kb_module.B},
                    "summary": summary, "rows": rows, "sweep": sweep}, indent=2))

    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    lines = [
        "# Question 2: Retrieval Evaluation",
        "",
        "Generated by `python scripts/eval_retrieval.py` from the gold set in `data/eval/retrieval_queries.json` "
        "(the labels were written before the retriever was tuned). Results are the **grounded** results the voice agent receives "
        f"(BM25 with title weight {kb_module.TITLE_WEIGHT}, query expansion, coverage factor; gates: score ≥ {kb_module.MIN_SCORE}, "
        f"coverage ≥ {kb_module.MIN_COVERAGE}).",
        "",
        "## Summary",
        "",
        "The **tuning** split was used to choose expansions, title weight and gates, so its score is optimistic. "
        "The **held-out** split was written afterwards and never used for tuning; it is the honest estimate.",
        "",
        "| Metric | Tuning split | Held-out split | All |", "|---|---|---|---|",
        "| Queries | " + " | ".join(str(m["queries"]) for m in (*splits.values(), summary)) + " |",
        "| Correct / partial / incorrect | " + " | ".join(
            f"{m['correct']} / {m['partially_correct']} / {m['incorrect']}" for m in (*splits.values(), summary)) + " |",
        "| Top-1 accuracy (answerable) | " + " | ".join(f"{m['top1_accuracy']:.0%}" for m in (*splits.values(), summary)) + " |",
        "| Recall@3 (answerable) | " + " | ".join(f"{m['recall_at_3']:.0%}" for m in (*splits.values(), summary)) + " |",
        "| MRR (answerable) | " + " | ".join(str(m["mrr"]) for m in (*splits.values(), summary)) + " |",
        "| Out-of-scope rejected | " + " | ".join(m["out_of_scope_rejected"] for m in (*splits.values(), summary)) + " |",
        "",
        "| Type | Queries | Correct | Partial | Incorrect |", "|---|---|---|---|---|",
    ]
    for t, rs in by_type.items():
        lines.append(f"| {t} | {len(rs)} | {sum(r['verdict'] == 'correct' for r in rs)} | "
                     f"{sum(r['verdict'] == 'partially correct' for r in rs)} | {sum(r['verdict'] == 'incorrect' for r in rs)} |")
    lines += ["", "## Results", "",
              "| ID | Split | Type | User question | Retrieved record (rank 1) | Source reference | Relevance explanation | Verdict |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        top = r["results"][0] if r["results"] else None
        record = f"`{top['record_id']}` {top['title']}: \"{top['content'][:110]}…\"" if top else "Nothing (gated)"
        source = f"`{top['source_ref']}`" if top else "n/a"
        lines.append(f"| {r['id']} | {r['split'].replace('_', '-')} | {r['type']} | {r['query']} | {record} | {source} | {explanation(r)} | **{r['verdict']}** |")

    lines += ["", "## Gate sensitivity", "",
              "Top-1 accuracy and out-of-scope rejection for each gate setting. The chosen setting is in bold. "
              "Margins are thin on this small set: the weakest in-scope coverage (F5, O5) is close to the best out-of-scope coverage (N4).",
              "", "| min_score | min_coverage | Top-1 | Recall@3 | Out-of-scope rejected |", "|---|---|---|---|---|"]
    for s in sweep:
        chosen = s["min_score"] == kb_module.MIN_SCORE and s["min_coverage"] == kb_module.MIN_COVERAGE
        row = f"{s['min_score']} | {s['min_coverage']} | {s['top1_accuracy']:.0%} | {s['recall_at_3']:.0%} | {s['out_of_scope_rejected']}"
        lines.append(f"| **{row.replace(' | ', '** | **')}** |" if chosen else f"| {row} |")
    lines += ["", "## Observations", "",
              "- Generic overlaps such as \"insurance\", \"price\" or \"company\" are rejected for out-of-scope questions because "
              "words the knowledge base has never seen (\"car\", \"planet\", \"CEO\") carry maximum IDF, which keeps coverage low.",
              "- Caller phrasing differs from policy wording (\"real person\" vs \"human advisor\", \"retired\" vs \"Senior Secure\", "
              "\"tell me\" vs \"state\"). A small expansion table bridges this; dense embeddings would generalize it in production.",
              "- Semantically overlapping records from different sources (e.g. the CSV plan summary and the web plan page) both rank "
              "highly. Both are correct and carry citations, so the agent can cite either.",
              "- Held-out failures show the limits of lexical retrieval: H5 (\"I'm 70, which plan can I get?\") needs numeric "
              "reasoning over age ranges, which belongs in the agent's qualification logic (step 3), not in retrieval; H7 "
              "(\"accident next week, covered right away?\") ranks the right FAQ first but has too little word overlap to pass "
              "the coverage gate, so the agent would escalate instead of answering. That is a safe failure, not a hallucination. "
              "Both are cases where dense embeddings would help.",
              "- This evaluation is a regression test (`tests/test_retrieval.py`), so changes to ingestion or ranking cannot "
              "silently reduce retrieval quality.",
              ""]
    (ROOT / "docs" / "RETRIEVAL_EVAL.md").write_text("\n".join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
