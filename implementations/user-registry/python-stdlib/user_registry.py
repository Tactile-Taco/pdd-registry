"""Candidate implementation for protocol user-registry v1.0.0 (variant: python-stdlib).

This is a CANDIDATE, not an admission. The Validation Engine decides.
Invariant lineage: every public method maps to protocol invariants S-001..S-003,
B-001..B-005, O-001..O-005 (see tests/ and the bundle).

Design notes (protocol-visible behavior only):
- B-001 idempotency: keyed on client_request_id; repeat returns the original record.
- B-002/B-005 uniqueness: emails normalized (strip + lower); conflicts are typed errors, no write.
- B-003 fail-closed: structural re-validation before any state change.
- O-001..O-004: stdlib only, no network, no filesystem, no background work.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Dict, Optional

# --- S-001 / S-002: schema-shaped validation (defense in depth) -------------

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_ALLOWED_ERROR_KINDS = {"invalid_request", "conflict", "not_found", "internal"}


class UserRegistryError(Exception):
    """Typed error carrying the protocol error envelope (S-002)."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in _ALLOWED_ERROR_KINDS:
            raise ValueError(f"unknown error kind: {kind}")
        self.kind = kind
        self.message = message
        super().__init__(message)


class User:
    __slots__ = ("id", "client_request_id", "email", "display_name", "created_at")

    def __init__(self, client_request_id: str, email: str, display_name: str,
                 now: Optional[str] = None) -> None:
        self.id = uuid.uuid4().hex
        self.client_request_id = client_request_id
        self.email = email
        self.display_name = display_name
        self.created_at = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def as_dict(self) -> Dict[str, str]:
        return {
            "id": self.id,
            "client_request_id": self.client_request_id,
            "email": self.email,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _validate_request(request) -> Optional[str]:
    """Structural validation mirroring schemas/request.schema.json (S-001).

    B-005 semantics: normalize (strip + lowercase) BEFORE validating, so
    whitespace/case variants are accepted and then collide by design.
    """
    if not isinstance(request, dict):
        return "request must be an object"
    for field in ("client_request_id", "email", "display_name"):
        if field not in request:
            return f"missing required field: {field}"
    req_id = request["client_request_id"]
    if not isinstance(req_id, str) or not (1 <= len(req_id) <= 128):
        return "client_request_id must be a string of 1..128 chars"
    email = request["email"].strip().lower() if isinstance(request["email"], str) else request["email"]
    if not isinstance(email, str) or not (3 <= len(email) <= 320) or not _EMAIL_RE.match(email):
        return "email must be a valid ASCII address of 3..320 chars"
    name = request["display_name"]
    if not isinstance(name, str) or not (1 <= len(name) <= 200):
        return "display_name must be a string of 1..200 chars"
    extra = set(request) - {"client_request_id", "email", "display_name"}
    if extra:
        return f"unknown fields: {sorted(extra)}"
    return None


class UserRegistry:
    """In-memory registry. At most one write per committed create (O: write budget 1)."""

    def __init__(self) -> None:
        self._by_request_id: Dict[str, User] = {}
        self._by_email: Dict[str, User] = {}

    def create(self, request) -> Dict[str, object]:
        """B-001..B-003: validate -> idempotent lookup -> uniqueness -> commit."""
        err = _validate_request(request)
        if err is not None:
            return self._error("invalid_request", err)  # B-003: no state change

        req_id = request["client_request_id"]
        existing = self._by_request_id.get(req_id)
        if existing is not None:
            # B-001: repeat of a committed request id returns the original record.
            return {"ok": True, "outcome": "existing", "user": existing.as_dict(), "error": None}

        normalized = _normalize_email(request["email"])
        dup = self._by_email.get(normalized)
        if dup is not None:
            # B-002/B-005: uniqueness violation -> typed error, no write.
            return self._error("conflict", "email already registered")

        user = User(req_id, normalized, request["display_name"])
        # One logical write (max_writes_per_request: 1).
        self._by_request_id[req_id] = user
        self._by_email[normalized] = user
        return {"ok": True, "outcome": "created", "user": user.as_dict(), "error": None}

    def get(self, client_request_id: str) -> Dict[str, object]:
        """B-004: deterministic read; never mutates state."""
        if not isinstance(client_request_id, str) or not (1 <= len(client_request_id) <= 128):
            return self._error("invalid_request", "client_request_id must be a string of 1..128 chars")
        user = self._by_request_id.get(client_request_id)
        if user is None:
            return self._error("not_found", "no user for client_request_id")
        return {"ok": True, "user": user.as_dict(), "error": None}

    def __len__(self) -> int:
        return len(self._by_request_id)

    @staticmethod
    def _error(kind: str, message: str) -> Dict[str, object]:
        return {"ok": False, "outcome": None, "user": None,
                "error": {"kind": kind, "message": message}}
