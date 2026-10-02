"""Q2 ingestion pipeline: mixed raw business content -> cleaned, traceable knowledge records.

Stages (each one is logged in data/kb/ingest_report.json):
  1. Extract   HTML pages/forms, PDF-text exports, Markdown, CSV tables, PDFs (failures are flagged).
  2. Clean     drop navigation/header/footer/cookie/promo blocks, running page headers, boilerplate
               sentences, and irrelevant sections; repair PDF hyphenation and line wrapping.
  3. Protect   detect and redact PII (emails, phones, SSNs, member IDs, named people).
  4. Normalize headings, terminology (glossary), dates (ISO 8601), times, and form-field names.
  5. Validate  quarantine records with obvious source errors (impossible dates, negative amounts,
               inverted ranges); exclude superseded document versions.
  6. Dedupe    remove exact/near-duplicate records and repeated sentences, keeping the most
               authoritative source.
  7. Publish   assign record IDs and write data/kb/records.jsonl.

Run with:  python -m app.ingest
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from app.market_vocab import PAGE_MARKER_RE, vocab
from app.models import KnowledgeRecord

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
KB_DIR = ROOT / "data" / "kb"
RECORDS_PATH = KB_DIR / "records.jsonl"


def market_dir(market: str) -> Path:
    return KB_DIR if market == "en" else KB_DIR / market


def records_path(market: str) -> Path:
    return market_dir(market) / "records.jsonl"


class ExtractionError(Exception):
    pass


@dataclass
class Section:
    heading: str
    text: str
    anchor: str
    raw_values: dict = field(default_factory=dict)  # table cells, kept for validation


@dataclass
class Document:
    title: str
    sections: list[Section]
    metadata: dict = field(default_factory=dict)  # effective_date, doc_version found in the text


# --------------------------------------------------------------------------- vocabularies


# canonical term -> raw variants observed across sources


# canonical form field -> label variants (matched as substrings of the normalized label)
FORM_FIELDS = {
    "full_name": ["your name", "full name", "name"],
    "date_of_birth": ["date of birth", "birth date", "dob"],
    "phone": ["phone number", "phone", "mobile"],
    "email": ["e-mail", "email"],
    "zip_code": ["zip code", "postal code", "zip"],
    "state": ["state of residence", "state"],
    "household_size": ["how many people need coverage", "household size"],
    "tobacco_use": ["tobacco use", "smoker"],
    "pre_existing_conditions": ["pre-existing", "pre existing", "medical conditions"],
    "current_insurance": ["current insurance", "existing coverage"],
    "preferred_callback_time": ["best time to call", "callback time"],
    "consent_to_contact": ["agree to be contacted", "consent"],
}

SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "button", "select"}
SKIP_CLASS_RE = re.compile(r"cookie|banner|breadcrumb|newsletter|sidebar|promo|testimonial|share", re.I)
VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}
BLOCK_TAGS = {"p", "li", "dd", "dt", "td", "th", "div", "section", "article"}


PII_PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    ("member_id", re.compile(r"\b[A-Z]{2,4}-\d{6,}\b"), "[MEMBER_ID]"),
    ("phone", re.compile(r"(?<![\d$])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"), "[PHONE]"),
    # Names are only redacted in labelled/attributed positions; each lookbehind must be fixed-width.
    ("person_name", re.compile(
        "(?:" + "|".join(rf"(?<=(?i:{label}))" for label in
                         ["— ", "supervisors: ", "of the month: ", "name: ", "customer: ", "member: ", "insured: "])
        + r")[A-Z][a-z]+(?: [A-Z][a-z]+)+"), "[PERSON]"),
]
PLACEHOLDER_RE = re.compile(r"\[(EMAIL|SSN|MEMBER_ID|PHONE|PERSON)\]")


# --------------------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:60].strip("-")


def standardize_heading(text: str, market: str = "en") -> str:
    text = re.sub(r"^\s*(#+|\d+(\.\d+)*\.?)\s*", "", text).strip().rstrip(":.")
    if text.isupper():
        text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = text[:1].upper() + text[1:]
    for plan in vocab(market)["proper_nouns"]:  # proper nouns keep their canonical casing
        text = re.sub(re.escape(plan), plan, text, flags=re.I)
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"(\[$])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def ensure_period(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def parse_date(text: str, market: str = "en") -> tuple[str | None, str | None]:
    """Return (iso_date, error) for the first date-like expression in text.

    Numeric order is market-specific: Indonesian sources write DD/MM/YYYY, so 01/02/2026
    is 1 February, while US and Philippine sources write MM/DD/YYYY.
    """
    v = vocab(market)
    months, month_re = v["months"], v["month_re"]
    numeric = ((lambda m: (m[3], m[2], m[1])) if v["day_first"] else (lambda m: (m[3], m[1], m[2])))
    patterns = [
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{month_re}\.?,?\s+(\d{{4}})\b", lambda m: (m[3], months[m[2][:3].lower()], m[1])),
        (rf"\b{month_re}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", lambda m: (m[3], months[m[1][:3].lower()], m[2])),
        (r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", numeric),
        (r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: (m[1], m[2], m[3])),
    ]
    for pattern, parts in patterns:
        match = re.search(pattern, text)
        if match:
            y, mo, d = (int(v) for v in parts(match))
            try:
                return date(y, mo, d).isoformat(), None
            except ValueError:
                return None, match[0]
    return None, None


def parse_metadata_line(line: str, market: str = "en") -> dict | None:
    """Recognize lines such as 'Document version 2.0 | Effective date: 05/01/2026'."""
    meta = {}
    for part in (p.strip() for p in line.split("|")):
        match = vocab(market)["metadata_re"].match(part)
        if not match:
            return None
        if match["date"]:
            iso, _ = parse_date(match["date"], market)
            if not iso:
                return None
            meta["effective_date"], meta["effective_date_raw"] = iso, match["date"]
        if match["version"]:
            meta["doc_version"] = match["version"]
    return meta


# --------------------------------------------------------------------------- extraction

class _HTMLExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title = ""
        self.in_title = False
        self.heading_tag: str | None = None
        self.heading_buf: list[str] = []
        self.text_buf: list[str] = []
        self.sections: list[list] = [["Overview", []]]
        self.in_form = False
        self.label_for: str | None = None
        self.label_buf: list[str] = []
        self.labels: dict[str, str] = {}
        self.fields: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip:
            if tag not in VOID_TAGS:
                self.skip += 1
            return
        if self.in_form and tag in {"input", "textarea", "select"} and a.get("type") not in {"hidden", "submit"}:
            self.fields.append({"id": a.get("id") or a.get("name"), "name": a.get("name"),
                                "type": a.get("type", tag), "required": "required" in a})
        if tag in SKIP_TAGS or SKIP_CLASS_RE.search(f"{a.get('class', '')} {a.get('id', '')}"):
            if tag not in VOID_TAGS:
                self.skip = 1
            return
        if tag == "title":
            self.in_title = True
        elif tag == "form":
            self.in_form = True
        elif tag == "label":
            self.label_for, self.label_buf = a.get("for"), []
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.heading_tag, self.heading_buf = tag, []
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
            return
        if tag == "title":
            self.in_title = False
        elif tag == "form":
            self.in_form = False
        elif tag == "label" and self.label_for:
            self.labels[self.label_for] = " ".join(self.label_buf).strip()
            self.label_for = None
        elif tag == self.heading_tag:
            heading = " ".join(self.heading_buf).strip()
            if tag == "h1":
                self.sections[0][0] = heading  # page heading names the intro section
            else:
                self.sections.append([heading, []])
            self.heading_tag = None
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
        elif self.heading_tag:
            self.heading_buf.append(data.strip())
        elif self.label_for:
            self.label_buf.append(data.strip())
        elif not self.in_form:
            self.text_buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.text_buf)).strip()
        if text:
            self.sections[-1][1].append(text)
        self.text_buf = []


def extract_html(raw: str, market: str = "en") -> tuple[Document, list[dict]]:
    parser = _HTMLExtractor()
    parser.feed(raw)
    parser._flush()
    metadata, sections, anchors = {}, [], Counter()
    for heading, blocks in parser.sections:
        kept = []
        for block in blocks:
            meta = parse_metadata_line(block, market)
            if meta is not None:
                metadata.update(meta)
            else:
                kept.append(ensure_period(block))
        if kept:
            anchors[slugify(heading)] += 1
            n = anchors[slugify(heading)]
            sections.append(Section(heading, " ".join(kept), slugify(heading) + (f"-{n}" if n > 1 else "")))
    fields = [{**f, "label": parser.labels.get(f["id"], "")} for f in parser.fields]
    page_title = parser.title.split("|")[0].strip() or parser.sections[0][0]
    return Document(page_title, sections, metadata), fields


def extract_text(raw: str, market: str = "en") -> Document:
    """Markdown and PDF-text exports: strip running headers/footers, fix wrapping, split on headings."""
    lines = [line.rstrip() for line in raw.splitlines()]
    counts = Counter(line.strip() for line in lines if line.strip())
    title = next((line.strip().lstrip("# ") for line in lines if line.strip()), "")
    metadata: dict = {}
    sections: list[list] = [["Overview", []]]
    paragraph: list[str] = []

    def end_paragraph():
        if paragraph:
            joined = ""
            for piece in paragraph:
                if joined.endswith("-") and piece[:1].islower():
                    joined = joined[:-1] + piece  # undo PDF line-break hyphenation
                else:
                    joined = f"{joined} {piece}".strip()
            sections[-1][1].append(joined)
            paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            end_paragraph()
            continue
        if PAGE_MARKER_RE.fullmatch(stripped) or counts[stripped] > 1:
            continue  # page numbers and running headers/footers repeated on every page
        if stripped.lstrip("# ") == title and not sections[-1][1] and len(sections) == 1:
            continue
        meta = parse_metadata_line(stripped, market)
        if meta is not None:
            metadata.update(meta)
            continue
        if re.match(r"^#{1,3}\s", stripped) or re.fullmatch(r"\d+(\.\d+)*\.?\s+[A-Z][A-Z0-9 ,&/()\-]+", stripped):
            end_paragraph()
            sections.append([stripped, []])
            continue
        paragraph.append(stripped)
    end_paragraph()
    result = [Section(standardize_heading(h, market), " ".join(ensure_period(b) for b in blocks),
                      slugify(standardize_heading(h, market)))
              for h, blocks in sections if blocks]
    return Document(standardize_heading(title, market), result, metadata)


TABLE_COLUMNS = {
    "plan name": "plan", "eligible ages": "ages", "premium (from)": "premium_from",
    "deductible": "deductible", "out-of-pocket maximum": "oop_max", "pcp copay": "pcp_copay",
    "generic rx copay": "rx_copay", "notes": "notes",
}


def extract_csv(raw: str) -> Document:
    reader = csv.reader(raw.splitlines())
    header = [normalize_terms(h.strip(), Counter()).lower() for h in next(reader)]
    keys = [TABLE_COLUMNS.get(h, slugify(h)) for h in header]
    sections = []
    for index, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        r = dict(zip(keys, (cell.strip() for cell in row)))
        text = (f"{r['plan']} plan summary: eligible ages {r['ages'].replace('-', ' to ')}. "
                f"Premium starts from {r['premium_from']} per month. Deductible: {r['deductible']}. "
                f"Out-of-pocket maximum: {r['oop_max']}. Primary care visit copay: {r['pcp_copay']}. "
                f"Generic prescription copay: {r['rx_copay']}. {ensure_period(r.get('notes', ''))}")
        sections.append(Section(f"{r['plan']} plan summary", text, f"row-{index}", raw_values=r))
    return Document("Plan Comparison Table", sections)


def extract_pdf(data: bytes, market: str = "en") -> Document:
    """Minimal text-layer reader. Image-only (scanned) PDFs are reported, never silently indexed."""
    if not data.startswith(b"%PDF"):
        raise ExtractionError("not a PDF file")
    streams = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        body = match.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        streams.append(body)
    text = " ".join(t.decode("latin-1") for s in streams for t in re.findall(rb"\((.*?)\)\s*Tj", s))
    if not text.strip():
        raise ExtractionError("PDF has no extractable text layer (scanned image); route to OCR or manual review")
    return extract_text(text, market)


# --------------------------------------------------------------------------- cleaning

def normalize_terms(text: str, counter: Counter, market: str = "en") -> str:
    for canonical, variants in vocab(market)["glossary"].items():
        for variant in sorted(variants, key=len, reverse=True):
            pattern = re.compile(rf"(?<![\w-]){re.escape(variant)}(?P<plural>e?s)?(?![\w-])", re.I)

            def repl(m, canonical=canonical, variant=variant):
                counter[f"{variant} -> {canonical}"] += 1
                term = canonical[:1].upper() + canonical[1:] if m[0][:1].isupper() else canonical
                return term + ("s" if m["plural"] else "")
            text = pattern.sub(repl, text)
    return text


def normalize_dates(text: str, log: list, errors: list, market: str = "en") -> str:
    def repl(match):
        iso, error = parse_date(match[0], market)
        if error:
            errors.append(f"invalid date: '{error}'")
            return match[0]
        log.append({"raw": match[0], "iso": iso})
        return iso
    month_re = vocab(market)["month_re"]
    pattern = (rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{month_re}\.?,?\s+\d{{4}}\b|"
               rf"\b{month_re}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{4}}\b")
    return re.sub(pattern, repl, text)


def redact_pii(text: str, found: Counter) -> str:
    for kind, pattern, placeholder in PII_PATTERNS:
        text, n = pattern.subn(placeholder, text)
        if n:
            found[kind] += n
    return text


def validate(section: Section, text: str) -> list[str]:
    """Obvious source errors. Records with errors are quarantined for owner review."""
    errors = []
    if re.search(r"-\s?\$\s?\d|\$\s?-\d", text):
        errors.append("negative monetary amount")
    for low, high in re.findall(r"aged (\d+) to (\d+)", text):
        if int(low) > int(high):
            errors.append(f"inverted age range {low} to {high}")
    ages = section.raw_values.get("ages", "")
    if re.fullmatch(r"\d+-\d+", ages) and int(ages.split("-")[0]) > int(ages.split("-")[1]):
        errors.append(f"inverted age range {ages}")
    return errors


def infer_category(heading: str, default: str, market: str = "en") -> str:
    for category, pattern in vocab(market)["category_rules"]:
        if re.search(pattern, heading, re.I):
            # A single-purpose FAQ page stays FAQ; only an objection heading may override it.
            return category if default != "faq" or category == "objection" else default
    return default


def detect_plan(*texts: str, market: str = "en") -> str | None:
    for text in texts:
        hits = [plan for plan in vocab(market)["proper_nouns"] if plan.lower() in text.lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-z0-9$]+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def form_section(fields: list[dict], report: dict) -> Section | None:
    if not fields:
        return None
    schema, described = [], []
    for f in fields:
        label = re.sub(r"\(.*?\)|[?#:]", " ", f["label"].lower())
        label = re.sub(r"\s+", " ", label).strip()
        canonical = next((key for key, variants in FORM_FIELDS.items()
                          for variant in sorted(variants, key=len, reverse=True) if variant in label), None)
        if canonical is None:
            report["unmapped_form_fields"].append(f)
            continue
        schema.append({"field": canonical, "raw_label": f["label"], "raw_name": f["name"],
                       "type": f["type"], "required": f["required"]})
        described.append((canonical.replace("_", " "), f["required"]))
    report["form_schema"] = schema
    # Group by requirement rather than tagging every field, so "required" is not repeated
    # once per field (that inflates term frequency for unrelated queries).
    required = ", ".join(name for name, req in described if req)
    optional = ", ".join(name for name, req in described if not req)
    text = f"The online quote form collects these required fields: {required}. Optional fields: {optional}."
    return Section("Lead intake form fields", text, "form-fields")


# --------------------------------------------------------------------------- pipeline

def build(raw_dir: Path | None = None, market: str = "en") -> tuple[list[KnowledgeRecord], dict]:
    raw_dir = raw_dir or (RAW_DIR if market == "en" else RAW_DIR / market)
    v = vocab(market)
    manifest = json.loads((raw_dir / "manifest.json").read_text())["sources"]
    superseded = {s["supersedes"]: s["path"] for s in manifest if s.get("supersedes")}
    report: dict = {
        "market": market, "language": v["language"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": [], "extraction_failures": [], "superseded": [], "irrelevant_sections": [],
        "boilerplate_removed": Counter(), "pii_redactions": Counter(), "terminology_normalizations": Counter(),
        "date_normalizations": [], "quarantined": [], "duplicates": [], "unmapped_form_fields": [],
        "form_schema": [],
    }
    candidates = []

    for order, src in enumerate(manifest):
        path = raw_dir / src["path"]
        entry = {"path": src["path"], "source_type": src["source_type"], "status": "ok", "sections": 0}
        report["sources"].append(entry)
        if src["path"] in superseded:
            entry["status"] = f"superseded by {superseded[src['path']]}"
            report["superseded"].append({"path": src["path"], "replaced_by": superseded[src["path"]]})
            continue
        try:
            if not path.exists():
                raise ExtractionError("file listed in manifest is missing")
            fields: list[dict] = []
            if src["source_type"] in {"web_page", "form"}:
                doc, fields = extract_html(path.read_text(encoding="utf-8"), market)
            elif src["source_type"] == "table":
                doc = extract_csv(path.read_text(encoding="utf-8"))
            elif src["source_type"] == "pdf":
                doc = extract_pdf(path.read_bytes(), market)
            else:
                doc = extract_text(path.read_text(encoding="utf-8"), market)
        except (ExtractionError, UnicodeDecodeError, csv.Error, KeyError, StopIteration) as exc:
            entry["status"] = "failed"
            report["extraction_failures"].append({"path": src["path"], "error": str(exc) or type(exc).__name__,
                                                  "action": "excluded from index; flagged for manual review"})
            continue

        if "effective_date" in doc.metadata:
            report["date_normalizations"].append({"raw": doc.metadata["effective_date_raw"],
                                                  "iso": doc.metadata["effective_date"], "source": src["path"]})
        sections = doc.sections
        if src["source_type"] == "form":
            sections = [s for s in [form_section(fields, report)] if s]
        entry["sections"] = len(sections)

        for index, section in enumerate(sections):
            source_ref = f"{src['path']}#{section.anchor}"
            heading = standardize_heading(section.heading, market)
            if v["irrelevant_heading_re"].search(heading):
                report["irrelevant_sections"].append({"source_ref": source_ref, "reason": "irrelevant heading"})
                continue
            kept = []
            for sentence in split_sentences(section.text):
                marketing_shout = v["drop_exclamations"] and sentence.endswith("!") and not re.search(r"\d", sentence)
                if v["boilerplate_re"].search(sentence) or marketing_shout:
                    report["boilerplate_removed"][sentence] += 1
                    continue
                kept.append(sentence)
            text = " ".join(kept)
            if len(v["domain_re"].findall(text)) < 1 and not validate(section, text) and not parse_date(text, market)[1]:
                report["irrelevant_sections"].append({"source_ref": source_ref, "reason": "no domain content"})
                continue

            pii = Counter()
            text = redact_pii(text, pii)
            # A sentence that is mostly redaction placeholders carries no knowledge.
            text = " ".join(s for s in split_sentences(text)
                            if len(PLACEHOLDER_RE.findall(s)) < 2 or len(PLACEHOLDER_RE.sub("", s).split()) >= 8)
            report["pii_redactions"].update(pii)

            text = normalize_terms(text, report["terminology_normalizations"], market)
            text = re.sub(r"\b(\d{1,2}) ?a\.m\.", r"\1 AM", text)
            text = re.sub(r"\b(\d{1,2}) ?p\.m\.", r"\1 PM", text)
            heading = normalize_terms(heading, report["terminology_normalizations"], market)
            errors = validate(section, text)
            text = normalize_dates(text, report["date_normalizations"], errors, market)
            if not text.strip():
                continue

            category = infer_category(heading, src["default_category"], market)
            plan = detect_plan(heading, doc.title, text, market=market) if category == "product" and market == "en" else None
            title = heading if not plan or plan.lower() in heading.lower() else f"{plan} - {heading}"
            candidate = {
                "title": title, "content": text, "category": category,
                "subcategory": slugify(plan) if plan else slugify(heading), "source_ref": source_ref,
                "source_type": src["source_type"], "source_origin": src["origin"],
                "version": doc.metadata.get("doc_version", src["doc_version"]),
                "effective_date": doc.metadata.get("effective_date", src.get("effective_date")), "pii_redacted": sorted(pii),
                "authority": src["authority"], "order": (order, index),
            }
            if errors:
                report["quarantined"].append({"source_ref": source_ref, "errors": errors, "content": text})
                continue
            candidates.append(candidate)

    # Most authoritative (lowest number), then newest, wins every duplicate decision.
    candidates.sort(key=lambda c: (c["authority"], -(int((c["effective_date"] or "0").replace("-", ""))), c["order"]))
    kept_records: list[dict] = []
    for cand in candidates:
        best = max(((similarity(cand["content"], k["content"]), k) for k in kept_records),
                   key=lambda item: item[0], default=(0.0, None))
        if best[0] >= 0.8:
            report["duplicates"].append({"level": "record", "dropped": cand["source_ref"],
                                         "kept": best[1]["source_ref"], "similarity": round(best[0], 2)})
            continue
        sentences = []
        for sentence in split_sentences(cand["content"]):
            match = next((k for k in kept_records for ks in split_sentences(k["content"])
                          if len(sentence.split()) >= 6 and similarity(sentence, ks) >= 0.75), None)
            if match:
                report["duplicates"].append({"level": "sentence", "dropped": cand["source_ref"],
                                             "kept": match["source_ref"], "text": sentence})
            else:
                sentences.append(sentence)
        if len(" ".join(sentences).split()) < 6:
            continue  # nothing new left after removing repeated sentences
        cand["content"] = " ".join(sentences)
        kept_records.append(cand)

    kept_records.sort(key=lambda c: (c["category"], c["order"]))
    per_category: Counter = Counter()
    records = []
    for cand in kept_records:
        per_category[cand["category"]] += 1
        records.append(KnowledgeRecord(
            record_id=f"kb_{cand['category']}_{per_category[cand['category']]:03d}",
            title=cand["title"], content=cand["content"], category=cand["category"],
            subcategory=cand["subcategory"], source_ref=cand["source_ref"], source_type=cand["source_type"],
            source_origin=cand["source_origin"], version=cand["version"], effective_date=cand["effective_date"],
            pii=any(p.search(cand["content"]) for _, p, _ in PII_PATTERNS), pii_redacted=cand["pii_redacted"],
            terms=sorted(t for t in [*v["glossary"], *v["proper_nouns"]] if t.lower() in cand["content"].lower()),
            content_hash=hashlib.sha256(cand["content"].encode()).hexdigest()[:16],
        ))

    report["totals"] = {
        "sources": len(manifest), "sources_failed": len(report["extraction_failures"]),
        "sources_superseded": len(report["superseded"]),
        "sections_extracted": sum(s["sections"] for s in report["sources"]),
        "irrelevant_sections_dropped": len(report["irrelevant_sections"]),
        "quarantined": len(report["quarantined"]),
        "duplicate_records_dropped": sum(d["level"] == "record" for d in report["duplicates"]),
        "duplicate_sentences_dropped": sum(d["level"] == "sentence" for d in report["duplicates"]),
        "records_published": len(records), "categories": dict(Counter(r.category for r in records)),
    }
    report["boilerplate_removed"] = dict(report["boilerplate_removed"])
    report["pii_redactions"] = dict(report["pii_redactions"])
    report["terminology_normalizations"] = dict(report["terminology_normalizations"])
    return records, report


def write(records: list[KnowledgeRecord], report: dict, out_dir: Path | None = None) -> None:
    out_dir = out_dir or market_dir(report.get("market", "en"))
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "records.jsonl").open("w") as fh:
        for record in records:
            fh.write(record.model_dump_json() + "\n")
    (out_dir / "ingest_report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "form_schema.json").write_text(json.dumps(report["form_schema"], indent=2))


def load_records(path: Path | None = None, market: str = "en") -> list[KnowledgeRecord]:
    path = path or records_path(market)
    return [KnowledgeRecord.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    import sys
    for mkt in (sys.argv[1:] or ["en", "ph", "id"]):
        records, report = build(market=mkt)
        write(records, report)
        print(f"[{mkt}] {json.dumps(report['totals'])}")
        print(f"      -> {records_path(mkt).relative_to(ROOT)}")
