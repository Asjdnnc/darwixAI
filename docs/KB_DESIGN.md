# Question 2: Knowledge-Base Design

The knowledge base turns mixed, messy business content into small, cited records that the Q1 voice agent can retrieve from. All source content is synthetic ("Darwix Health" health-insurance lead qualification) and contains no real customer data.

```bash
python -m app.ingest      # data/raw/** -> data/kb/records.jsonl, ingest_report.json, form_schema.json
pytest tests/test_ingest.py
```

## 1. Inputs

Every raw file is registered in `data/raw/manifest.json`, which records its type, original location, document version, authority rank, default category, and any older version it supersedes.

| File | Input type | Deliberate problems it contains |
|---|---|---|
| `web/plans_overview.html` | Web page | Cookie banner, navigation, breadcrumb, marketing lines, testimonial with a customer name and member ID, newsletter form, footer, inconsistent terms ("deductable", "OOP max", "monthly fee") |
| `web/family_shield.html` | Web page | Near-duplicate copy of the overview page, "share" block, promo sidebar, US date format |
| `web/faq.html` | Web page | The same FAQ appears twice; "waiting time" vs "waiting period" |
| `web/careers.html` | Web page | Irrelevant content |
| `forms/lead_intake_form.html` | Form | Inconsistent field labels ("DOB", "Phone #", "Zip", "Smoker?") |
| `tables/plan_comparison.csv` | Table | Exact duplicate row, a negative deductible (source error), "Family-Shield" |
| `docs/underwriting_policy_v2.txt` | PDF text export | Running page headers and footers, "Page x of y", words hyphenated across lines, an impossible date ("February 30"), a supervisor's name, email, and phone |
| `docs/underwriting_policy_v1.txt` | PDF text export | Older version that v2 supersedes, with conflicting rules |
| `docs/sales_playbook.md` | Markdown | Objection handling, a sentence duplicated from the policy, internal notes containing PII |
| `docs/scanned_brochure.pdf` | PDF | Image only with no text layer, so extraction fails |

## 2. Extraction and cleaning (`app/ingest.py`)

| Stage | What happens |
|---|---|
| Website extraction | A standard-library `HTMLParser` skips `nav/header/footer/aside/script/style/button/select` and any element whose class or id matches cookie, banner, breadcrumb, newsletter, sidebar, promo, testimonial, or share. Pages are split into sections on `h2`/`h3`, and the `h1` names the intro section. |
| Document parsing | Lines repeated on every page (running headers and footers) and `Page x of y` markers are dropped. Words hyphenated across a line break are rejoined ("fol-/low" becomes "follow"). Wrapped lines are merged into paragraphs. Sections are split on Markdown `#` or numbered ALL-CAPS headings. |
| Tables | Column names are normalized, and each row becomes one readable sentence-style record that the agent can speak aloud. |
| Forms | Each label is linked to its input and mapped to a canonical field name; the result is written to `form_schema.json` and to one qualification record. |
| Extraction failures | A missing file, a decode error, or a PDF without a text layer is logged in `extraction_failures` and kept out of the index, never indexed silently. |
| Boilerplate | Sentences matching footer, cookie, or call-to-action patterns are removed, as are promotional exclamations with no numbers. Sections with irrelevant headings or no domain vocabulary are dropped. |
| Metadata lines | Lines such as `Last updated: 5th Mar 2026` or `Document version 2.0 \| Effective date: 05/01/2026` are taken out of the content and stored as `effective_date` and `version`. |
| PII | Emails, phone numbers, SSNs, member IDs, and people's names in labelled or attributed positions are replaced with `[EMAIL]`, `[PHONE]`, `[SSN]`, `[MEMBER_ID]`, and `[PERSON]`. A sentence that is mostly placeholders is dropped. Each record lists the PII types removed in `pii_redacted`, and `pii` says whether any PII is still present (it must be `false`). |
| Standardization | Terms are rewritten to canonical forms from a glossary (for example "pre existing disease" and "pre-existing illness" become "pre-existing condition", and "network hospital" becomes "in-network provider"), keeping capitalization and plurals. Dates become ISO 8601 (US sources are read as MM/DD/YYYY). "a.m./p.m." becomes "AM/PM". Headings become sentence case, and plan names keep their official casing. |
| Source errors | Impossible dates, negative amounts, and inverted age ranges send the section to `quarantined` for the content owner to fix. They are never indexed. |
| Versioning | A source listed in another source's `supersedes` field is excluded entirely. |
| Deduplication | Duplicate checks look at the most authoritative source first (authority 1 = compliance policy, 2 = playbook or product table, 3 = website), then the newest. A record is dropped when its word-3-gram Jaccard similarity to a kept record is 0.8 or higher. A sentence of six or more words is dropped when its similarity to a sentence in a kept record is 0.75 or higher. Every decision is logged with the dropped and the kept `source_ref`. |

### Latest run (`data/kb/ingest_report.json`)

| Metric | Value |
|---|---|
| Sources registered | 10 (1 extraction failure, 1 superseded) |
| Sections extracted | 42 |
| Irrelevant sections dropped | 7 (purpose, internal notes, share, careers, and similar) |
| Quarantined for source errors | 2 (`plan_comparison.csv#row-3` negative deductible; `underwriting_policy_v2.txt#errata` February 30) |
| Duplicates removed | 2 records, 4 sentences |
| PII redacted | 1 email, 1 phone, 1 person name |
| Terminology fixes | 14 distinct variant-to-canonical rewrites |
| **Records published** | **30**: product 9, faq 7, objection 6, qualification 4, policy 3, process 1 |

## 3. Record schema (`app/models.py::KnowledgeRecord`)

| Field | Example | Purpose |
|---|---|---|
| `record_id` | `kb_policy_001` | Stable ID, `kb_<category>_<nnn>` |
| `title` | `Mandatory disclosures` | Standardized heading |
| `content` | cleaned, redacted, normalized text | What gets retrieved and quoted |
| `category` / `subcategory` | `policy` / `mandatory-disclosures` | Taxonomy (below) |
| `source_ref` | `docs/underwriting_policy_v2.txt#mandatory-disclosures` | Raw file plus section anchor; used as the citation |
| `source_type` / `source_origin` | `pdf_extract` / `SharePoint > Compliance > Underwriting_Policy_v2.pdf` | Where the content came from |
| `version` / `effective_date` | `2.0` / `2026-05-01` | Versioning |
| `pii` / `pii_redacted` | `false` / `["email","person_name","phone"]` | PII protection evidence |
| `terms` | `["medical underwriting"]` | Canonical glossary terms present |
| `content_hash` | `sha256[:16]` | Change detection on re-ingest |

Sample record:

```json
{"record_id": "kb_policy_003", "title": "Human escalation", "category": "policy", "subcategory": "human-escalation",
 "content": "Transfer the caller to a licensed human advisor when the caller asks for a person, raises a complaint, asks a detailed medical or legal question, or disputes a claim on an existing policy. Human advisors are available Monday to Friday 8 AM to 8 PM Central Time and Saturday 9 AM to 2 PM Central Time. Outside these hours, schedule a callback within 1 business day.",
 "source_ref": "docs/underwriting_policy_v2.txt#human-escalation", "source_type": "pdf_extract",
 "source_origin": "SharePoint > Compliance > Underwriting_Policy_v2.pdf", "version": "2.0", "effective_date": "2026-05-01",
 "pii": false, "pii_redacted": ["email", "person_name", "phone"]}
```

## 4. Taxonomy

| Category | Contents | Example records |
|---|---|---|
| `product` | Plan features, costs, who is covered, payment options | Essential Care, Family Shield, Senior Secure, plan summaries |
| `policy` | Compliance rules the agent must follow | Mandatory disclosures, prohibited statements, human escalation |
| `qualification` | Eligibility and lead-qualification rules, question order, form fields | Eligibility rules, preliminary lead qualification |
| `faq` | Customer questions with approved answers | Waiting period, pre-existing conditions, claims, cancellation, grace period, states |
| `objection` | Approved objection-handling guidance | Too expensive, employer coverage, spouse, claims distrust, privacy, scam |
| `process` | Call script steps | Call opening script |

A record's category comes from its heading (for example `^Objection` gives objection, and `disclosure`, `prohibited`, or `escalation` give policy), with the manifest's `default_category` as the fallback. For product records, `subcategory` is the plan slug.

## 5. Chunking and indexing

- **Chunking strategy:** each record is a single logical section (one FAQ answer, one objection, one plan, one rule block), so records are already small. `KnowledgeBase.upsert` splits only on sentence boundaries, with at most 80 words per chunk and a one-sentence overlap when a record is longer. The current corpus produces 32 chunks, the largest being 72 words. That size suits spoken answers and small-model context windows.
- **Chunk metadata:** `chunk_id` (`<record_id>#c<n>`), `record_id`, `title`, `category`, `version`, and `source_ref` travel with every chunk, so a retrieved chunk can always be cited.
- **Re-ingesting:** `upsert` replaces every chunk of a `record_id`. `content_hash` shows which records changed between runs, so only those would need re-embedding.
- **Index:** BM25 (k1 = 1.2, b = 0.75) over stemmed tokens with stop words removed. The record title counts 4 times, so a question naming a plan or topic prefers that record. The index is rebuilt on every `upsert`.
- **Query expansion:** a small table maps caller phrasing onto knowledge-base vocabulary ("how much" to premium, "real person" to human advisor, "retired" to senior, "required" to must/mandatory). Expansion terms count half as much, except when none of the caller's own words occur in the knowledge base; then they count fully.
- **Ranking:** score = BM25 × (0.5 + 0.5 × coverage). Coverage is the IDF-weighted share of the question's informative terms that a chunk explains. Words the knowledge base has never seen carry maximum IDF, so a match on a generic word like "insurance" scores low coverage for "Do you sell car insurance?". Only the best chunk per record is returned, and results below 40% of the top score are dropped.
- **Grounding gates:** `/agent/turn` uses `retrieve(..., grounded_only=True)`, which requires score ≥ 2.5 and coverage ≥ 0.4. If nothing passes, the agent gets no context and must say the information is unavailable. `/retrieve` is ungated for operator search and supports `category=` and `grounded_only=` filters.
- **Evaluation:** `python scripts/eval_retrieval.py` produces `docs/RETRIEVAL_EVAL.md`. On a tuning split of 28 queries it scores 100% top-1; on a held-out split of 14 queries it scores 82% top-1 and rejects 7 of 7 out-of-scope questions overall. `tests/test_retrieval.py` fails if either split drops.
- **Production path:** add dense embeddings (for example `text-embedding-3-small` or `bge-small` in pgvector) to the BM25 score using reciprocal-rank fusion, add a cross-encoder reranker, filter to the latest version by `effective_date`, and put the gold set in CI. Embeddings would fix the two held-out misses.

## 6. Citation method

The agent receives the retrieved chunks with their `source_ref`, and the model returns the indexes of the chunks it used. The API maps those indexes back to `source_ref` values, so a citation can never point to a chunk that wasn't retrieved. If no chunk is cited, the reply is treated as unsupported and escalated.

## 7. Known limitations

- Detecting people's names relies on labels and attribution. A production system would add an NER model such as Presidio before indexing free text like call notes.
- The PDF reader only handles text layers; scanned PDFs need OCR such as Tesseract or Textract.
- Near-duplicates are detected lexically. Paraphrases with different wording (for example the CSV plan summary and the web plan page) are kept as separate records; embedding similarity would catch them.
- Conflicting facts across sources are resolved by authority and recency during deduplication, but conflicts with different wording are not yet detected automatically.
