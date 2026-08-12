"""Checkpoint journal for the resumable backlog runner.

Per-file state: status (pending|done|failed|skipped), pass versions that were
applied, attempt count, last error. Single JSON document, rewritten atomically
(tmp + rename) so a crash never corrupts it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time


class CheckpointJournal:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._data: dict = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    def _save(self) -> None:
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".journal-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, sort_keys=True, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _key(self, source: str, filename: str) -> str:
        return f"{source}/{filename}"

    def get(self, source: str, filename: str) -> dict:
        return self._data.get(self._key(source, filename), {})

    def set(self, source: str, filename: str, **fields) -> None:
        key = self._key(source, filename)
        entry = self._data.setdefault(key, {})
        entry.update(fields)
        entry["source"] = source
        entry["filename"] = filename
        entry["updated_at"] = time.time()
        self._save()

    def mark_pending(self, source: str, filename: str, attempts: int | None = None) -> None:
        e = self.get(source, filename)
        self.set(source, filename, status="pending",
                 attempts=attempts if attempts is not None else e.get("attempts", 0))

    def mark_done(self, source: str, filename: str, pass_versions: dict) -> None:
        self.set(source, filename, status="done", pass_versions=pass_versions)

    def mark_failed(self, source: str, filename: str, error: str) -> None:
        e = self.get(source, filename)
        self.set(source, filename, status="failed",
                 attempts=int(e.get("attempts", 0)) + 1, last_error=str(error)[:500])

    def is_done(self, source: str, filename: str, required_versions: dict) -> bool:
        e = self.get(source, filename)
        if e.get("status") != "done":
            return False
        applied = e.get("pass_versions", {})
        return all(applied.get(k) == v for k, v in required_versions.items())

    def needs_work(self, source: str, filename: str, required_versions: dict,
                   max_attempts: int = 3) -> bool:
        e = self.get(source, filename)
        if self.is_done(source, filename, required_versions):
            return False
        if e.get("status") == "failed" and int(e.get("attempts", 0)) >= max_attempts:
            return False
        return True
