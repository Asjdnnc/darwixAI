import json

import pytest

from app.ingest import build, normalize_dates, normalize_terms, redact_pii
from collections import Counter


@pytest.fixture(scope="module")
def built():
    return build()


def by_ref(records):
    return {r.source_ref: r for r in records}


def test_every_record_is_traceable(built):
    records, _ = built
    assert len(records) >= 25
    ids = [r.record_id for r in records]
    assert len(ids) == len(set(ids))
    for r in records:
        assert r.record_id.startswith(f"kb_{r.category}_")
        assert "#" in r.source_ref and r.source_origin and r.version and r.content_hash
        assert r.pii is False


def test_all_required_categories_are_covered(built):
    categories = {r.category for r in built[0]}
    assert {"product", "policy", "qualification", "faq", "objection"} <= categories


def test_navigation_footer_and_marketing_are_removed(built):
    text = " ".join(r.content for r in built[0]).lower()
    for noise in ["cookies", "all rights reserved", "member login", "call us today", "newsletter",
                  "fitness tracker", "page 1 of", "do not distribute", "hiring"]:
        assert noise not in text


def test_superseded_and_failed_sources_are_excluded(built):
    records, report = built
    assert not any("underwriting_policy_v1" in r.source_ref for r in records)
    assert report["superseded"][0]["path"] == "docs/underwriting_policy_v1.txt"
    assert report["extraction_failures"][0]["path"] == "docs/scanned_brochure.pdf"


def test_source_errors_are_quarantined(built):
    records, report = built
    quarantined = {q["source_ref"] for q in report["quarantined"]}
    assert {"tables/plan_comparison.csv#row-3", "docs/underwriting_policy_v2.txt#errata"} <= quarantined
    assert not any(ref in by_ref(records) for ref in quarantined)


def test_duplicates_are_removed(built):
    records, report = built
    refs = [r.source_ref for r in records]
    assert "web/faq.html#is-there-a-waiting-period-before-my-coverage-starts-2" not in refs
    assert "tables/plan_comparison.csv#row-4" not in refs
    sentences = [s for r in records for s in r.content.split(". ")]
    assert sum("subject to medical underwriting and depend on verified medical history" in s for s in sentences) == 1


def test_pii_is_redacted_and_recorded(built):
    records, report = built
    escalation = by_ref(records)["docs/underwriting_policy_v2.txt#human-escalation"]
    assert "priya" not in escalation.content.lower() and "555-0142" not in escalation.content
    assert set(escalation.pii_redacted) == {"email", "person_name", "phone"}


def test_pdf_hyphenation_and_terminology_are_repaired(built):
    text = " ".join(r.content for r in built[0])
    assert "eligible dependents" in text and "guarantee approval or coverage of a pre-existing condition" in text
    assert "- " not in text.replace(" - ", "")
    for variant in ["deductable", "dependant", "oop max", "pre existing disease", "monthly fee", "FamilyShield"]:
        assert variant.lower() not in text.lower()


def test_form_fields_are_standardized(built):
    schema = {f["field"] for f in built[1]["form_schema"]}
    assert {"full_name", "date_of_birth", "phone", "zip_code", "tobacco_use", "consent_to_contact"} <= schema
    assert built[1]["unmapped_form_fields"] == []


def test_unit_normalizers():
    assert normalize_dates("on 5th Mar 2026 and 03/05/2026", [], []) == "on 2026-03-05 and 2026-03-05"
    errors = []
    normalize_dates("February 30, 2026", [], errors)
    assert errors == ["invalid date: 'February 30, 2026'"]
    assert normalize_terms("Pre-existing illnesses and network hospitals", Counter()) == \
        "Pre-existing conditions and in-network providers"
    found = Counter()
    assert redact_pii("Reach jane@x.example or (512) 555-0142, SSN 123-45-6789", found) == \
        "Reach [EMAIL] or [PHONE], SSN [SSN]"


def test_published_file_matches_build(built):
    from app.ingest import RECORDS_PATH
    published = [json.loads(line)["record_id"] for line in RECORDS_PATH.read_text().splitlines()]
    assert published == [r.record_id for r in built[0]]
