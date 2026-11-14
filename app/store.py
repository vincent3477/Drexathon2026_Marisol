"""Thread-safe, capped, in-memory store for triaged uploads.

Deliberately small (a dict + a lock behind add/get/list) so a real database
can replace it later without touching the rest of the code. Nothing here
writes to disk; everything is lost when the process restarts.
"""

import threading
import time
import uuid

DEFAULT_CAP = 200


class ReportStore:
    def __init__(self, cap: int = DEFAULT_CAP):
        self._cap = cap
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._order: list[str] = []  # ids in insertion order (oldest first)

    def add(self, record: dict) -> dict:
        """Store one record; assigns id/timestamp if absent. Returns it."""
        record = dict(record)
        record.setdefault("id", uuid.uuid4().hex)
        record.setdefault("created_at", time.time())
        with self._lock:
            self._records[record["id"]] = record
            self._order.append(record["id"])
            while len(self._order) > self._cap:  # drop oldest beyond cap
                old = self._order.pop(0)
                self._records.pop(old, None)
        return record

    def get(self, report_id: str) -> dict | None:
        with self._lock:
            return self._records.get(report_id)

    def list(self) -> list[dict]:
        """Newest first, without image bytes (the /image endpoint serves those)."""
        with self._lock:
            out = []
            for rid in reversed(self._order):
                rec = dict(self._records[rid])
                rec.pop("image_bytes", None)
                rec.pop("content_type", None)
                out.append(rec)
            return out
