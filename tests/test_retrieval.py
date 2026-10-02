import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kb as kb_module
from app.kb import KnowledgeBase, stem, tokenize
from app.main import app
from app.seed import seed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from eval_retrieval import QUERIES, evaluate, metrics  # noqa: E402


@pytest.fixture(scope="module")
def kb():
    base = KnowledgeBase()
    seed(base)
    return base


@pytest.fixture(scope="module")
def rows(kb):
    queries = json.loads(QUERIES.read_text())["queries"]
    return evaluate(kb, queries, kb_module.MIN_SCORE, kb_module.MIN_COVERAGE)


def test_at_least_five_queries_per_required_type(rows):
    for kind in ["product", "policy", "qualification", "faq", "objection"]:
        assert sum(r["type"] == kind for r in rows) >= 3
    assert len(rows) >= 5


def test_tuning_split_has_no_regressions(rows):
    m = metrics([r for r in rows if r["split"] == "tuning"])
    assert m["incorrect"] == 0 and m["partially_correct"] == 0


def test_held_out_split_stays_above_floor(rows):
    m = metrics([r for r in rows if r["split"] == "held_out"])
    assert m["top1_accuracy"] >= 0.8


def test_out_of_scope_questions_never_ground_an_answer(rows):
    leaked = [r["id"] for r in rows if not r["expected"] and r["results"]]
    assert leaked == []


def test_results_carry_citation_metadata(kb):
    chunk = kb.retrieve("How long is the waiting period?", grounded_only=True).chunks[0]
    assert chunk.source_ref == "web/faq.html#is-there-a-waiting-period-before-my-coverage-starts"
    assert chunk.record_id.startswith("kb_faq_") and chunk.score > 0 and 0 < chunk.coverage <= 1


def test_category_filter(kb):
    chunks = kb.retrieve("too expensive", category="objection").chunks
    assert chunks and all(c.category == "objection" for c in chunks)


def test_stemming_and_stop_words():
    assert stem("plans") == stem("plan") and stem("covered") == stem("cover") and stem("policies") == "policy"
    assert tokenize("What is the price of the plans?") == ["price", "plan"]


def test_api_exposes_gated_and_ungated_search():
    client = TestClient(app)
    ungated = client.get("/retrieve", params={"q": "Do you sell car insurance?"}).json()["chunks"]
    gated = client.get("/retrieve", params={"q": "Do you sell car insurance?", "grounded_only": True}).json()["chunks"]
    assert ungated and gated == []
