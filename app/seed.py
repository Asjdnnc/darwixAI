from app.ingest import RECORDS_PATH, build, load_records
from app.kb import KnowledgeBase


def seed(kb: KnowledgeBase) -> None:
    """Load the published knowledge records; rebuild from data/raw if they are missing."""
    records = load_records() if RECORDS_PATH.exists() else build()[0]
    for record in records:
        kb.upsert(record)


if __name__ == "__main__":
    kb = KnowledgeBase()
    seed(kb)
    print(f"Loaded {len(kb.records)} records as {len(kb.chunks)} chunks.")
