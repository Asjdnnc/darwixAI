from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


Category = Literal["product", "policy", "qualification", "faq", "objection", "process"]


class KnowledgeRecord(BaseModel):
    """One cleaned, traceable knowledge-base record (see docs/KB_DESIGN.md)."""
    record_id: str  # e.g. kb_product_001
    title: str
    content: str
    category: Category
    subcategory: str  # plan slug or topic slug
    source_ref: str  # raw file path + section anchor, e.g. web/faq.html#can-i-cancel-my-policy
    source_type: str  # web_page | pdf_extract | pdf | markdown | table | form
    source_origin: str  # original URL or document-system location
    version: str = "1.0"
    effective_date: str | None = None  # ISO 8601
    pii: bool = False  # True only if PII is still present after redaction
    pii_redacted: list[str] = []  # PII types removed during ingestion
    terms: list[str] = []  # canonical glossary terms present
    content_hash: str = ""


class Chunk(BaseModel):
    chunk_id: str
    record_id: str
    title: str
    content: str
    source_ref: str
    category: str
    version: str
    score: float | None = None  # BM25 score, set at retrieval time
    coverage: float | None = None  # share of informative query terms explained, set at retrieval time


class RetrievalResult(BaseModel):
    query: str
    chunks: list[Chunk]


class AgentTurn(BaseModel):
    call_id: str = "web-demo"
    customer_text: str = Field(min_length=1, max_length=4000)
    market: Literal["en", "ph", "id"] = "en"


class TranscriptEvent(BaseModel):
    call_id: str
    speaker: Literal["agent", "customer", "unknown"] = "unknown"
    market: Literal["en", "ph", "id"] = "en"
    text: str
    received_at: datetime = Field(default_factory=utc_now)


class Signal(BaseModel):
    topic: Literal["compliance_gap", "cross_sell", "frustration", "payment_difficulty", "callback"]
    confidence: float = Field(ge=0, le=1)
    evidence: str


class Nudge(BaseModel):
    nudge_id: str = Field(default_factory=lambda: str(uuid4()))
    call_id: str
    topic: str
    message: str
    confidence: float
    created_at: datetime = Field(default_factory=utc_now)
    expires_in_seconds: int = 90
