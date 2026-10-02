"""Mock CRM: the voice agent's business actions (Q1 optional requirement).

Leads, callbacks, and escalations are written to JSON files under data/crm/. If CRM_WEBHOOK_URL
is set, every action is also POSTed there (for example to a webhook.site URL, Zapier, or a
real CRM); webhook failures are recorded but never break the call.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MockCRM:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or ROOT / "data" / "crm"
        self._lock = threading.Lock()

    def _path(self, kind: str) -> Path:
        return self.directory / f"{kind}.json"

    def _read(self, kind: str) -> dict:
        path = self._path(kind)
        return json.loads(path.read_text()) if path.exists() else {}

    def _write(self, kind: str, key: str, item: dict) -> dict:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            items = self._read(kind)
            created = key not in items
            items[key] = {**items.get(key, {}), **item, "updated_at": now_iso()}
            items[key].setdefault("created_at", items[key]["updated_at"])
            self._path(kind).write_text(json.dumps(items, indent=2))
        result = {"type": kind[:-1], "id": key, "operation": "created" if created else "updated"}
        result["webhook"] = self._notify(kind[:-1], items[key])
        return result

    def _notify(self, event: str, payload: dict) -> str:
        if not settings.crm_webhook_url:
            return "not configured"
        body = json.dumps({"event": event, "data": payload}).encode()
        request = urllib.request.Request(settings.crm_webhook_url, data=body,
                                         headers={"content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return f"delivered ({response.status})"
        except Exception as exc:  # never fail the call because the CRM is down
            return f"failed ({type(exc).__name__})"

    def upsert_lead(self, call_id: str, lead: dict) -> dict:
        return self._write("leads", f"lead_{call_id[:8]}", {"call_id": call_id, **lead})

    def schedule_callback(self, call_id: str, callback: dict) -> dict:
        return self._write("callbacks", f"cb_{call_id[:8]}", {"call_id": call_id, **callback})

    def escalate(self, call_id: str, escalation: dict) -> dict:
        return self._write("escalations", f"esc_{call_id[:8]}", {"call_id": call_id, **escalation})

    def record(self, kind: str, call_id: str, data: dict) -> dict:
        """Generic record for market flows: promises to pay, call notes."""
        prefix = {"promises": "ptp", "notes": "note"}.get(kind, kind[:3])
        return self._write(kind, f"{prefix}_{call_id[:8]}", {"call_id": call_id, **data})

    def list(self, kind: str) -> list[dict]:
        return [{"id": key, **value} for key, value in self._read(kind).items()]
