"""pdd-registry candidate: pure in-memory catalog search + read views.

The production surface (src/server.py + src/registry_index.py) is the
deployment wiring; this candidate is the attestable core that defines the
search/view semantics of the pdd-registry protocol. Stdlib only, no file or
network I/O (O-001/O-002/O-003), no background work (O-004).

The catalog is a list of bundle dicts in the shape `registry_index.load_catalog`
produces ({name, version, status, purpose, depends_on, provides, invariants,
capabilities, ...}); entries carrying an 'error' key are broken bundles and
are skipped by search/listing, exactly like the shared index.
"""

import json  # noqa: F401  (allowlisted import; schema helpers live in tests)
import re
from dataclasses import dataclass, field

ERROR_KINDS = ("invalid_request", "not_found", "internal")
SEVERITIES = ("must", "should")
LAYERS = ("structural", "behavioral", "operational")

# Field weights — parity with registry_index._FIELD_WEIGHT: name > purpose >
# invariant > capability. Scores are the weight (no token multiplier).
WEIGHTS = {"name": 10, "purpose": 5, "invariant": 3, "capability": 2}

_WORD = re.compile(r"[a-z0-9_]+")


def _error(kind: str, message: str) -> dict:
    """S-002: the stable error envelope with an enumerated kind."""
    return {"ok": False, "error": {"kind": kind, "message": message}}


@dataclass
class Registry:
    """Catalog search + read views. All methods are pure (no state mutation)."""

    catalog: list = field(default_factory=list)

    def _entries(self, b: dict) -> list:
        """Searchable entries — parity with registry_index._index_entries:
        separate name and purpose entries (field 'bundle'), one entry per
        invariant ('id: statement'), one per capability key."""
        out = []
        name = b.get("name", "")
        if name:
            out.append((name, "bundle", "name", name, WEIGHTS["name"]))
            purpose = b.get("purpose")
            if purpose:
                out.append((name, "bundle", "purpose", purpose, WEIGHTS["purpose"]))
        for layer in LAYERS:
            for it in (b.get("invariants") or {}).get(layer, []) or []:
                iid = it.get("id")
                if iid:
                    text = f"{iid}: {it.get('statement') or ''}"
                    out.append((name, layer, iid, text, WEIGHTS["invariant"]))
        for key in b.get("capabilities") or {}:
            out.append((name, "capabilities", str(key), str(key), WEIGHTS["capability"]))
        return out

    def search(self, query: str = "", limit: int = 20) -> dict:
        """Ranked keyword search. B-003: blank/non-word queries fail closed."""
        q = query.strip() if isinstance(query, str) else ""
        if not q:
            return _error("invalid_request", "query must be a non-blank string")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return _error("invalid_request", "limit must be a positive integer")
        tokens = _WORD.findall(q.lower())
        if not tokens:
            return _error("invalid_request", "query must contain word characters")
        results = []
        for b in self.catalog:
            if not isinstance(b, dict) or "error" in b:
                continue  # broken bundles are skipped (shared-index parity)
            for bname, layer, iid, text, weight in self._entries(b):
                hay = text.lower()
                if all(tok in hay for tok in tokens):
                    results.append({"bundle": bname, "layer": layer, "id": iid,
                                    "text": text, "score": weight})
        # B-001: deterministic order — score desc, then (bundle, layer, id).
        results = self._stable_sort(results)  # B-001
        return {"ok": True, "query": q, "count": len(results),
                "results": results[:limit], "error": None}

    def _stable_sort(self, results: list) -> list:
        # B-001 determinism guard: a mutant removing this line must fail
        # test_B001_deterministic_search (stable order, no mutation).
        return sorted(results, key=lambda e: (-e["score"], e["bundle"], e["layer"], e["id"]))

    def bundles(self, status: str | None = None, depends_on: str | None = None) -> dict:
        """Filtered listing; filters are exact-match (B-004)."""
        out = []
        for b in self.catalog:
            if not isinstance(b, dict) or "error" in b:
                continue
            if status is not None and b.get("status") != status:
                continue
            if depends_on is not None and depends_on not in (b.get("depends_on") or []):
                continue
            out.append({"name": b.get("name"), "version": b.get("version"),
                        "status": b.get("status"),
                        "depends_on": b.get("depends_on") or [],
                        "provides": b.get("provides") or {}})
        return {"ok": True, "bundles": out, "count": len(out), "error": None}

    def bundle_summary(self, name: str) -> dict:
        """Single-bundle summary; unknown name → not_found (B-003)."""
        for b in self.catalog:
            if isinstance(b, dict) and b.get("name") == name and "error" not in b:
                return {"ok": True, "bundle": b.get("name"), "version": b.get("version"),
                        "status": b.get("status"), "purpose": b.get("purpose"),
                        "depends_on": b.get("depends_on") or [],
                        "provides": b.get("provides") or {}, "error": None}
        return _error("not_found", f"no bundle named {name!r}")

    def invariants_view(self, name: str, severity: str | None = None) -> dict:
        """S/B/O invariants, optionally severity-filtered (B-005)."""
        if severity is not None and severity not in SEVERITIES:
            return _error("invalid_request", "severity must be one of must/should")
        for b in self.catalog:
            if isinstance(b, dict) and b.get("name") == name and "error" not in b:
                view = {}
                for layer in LAYERS:
                    items = (b.get("invariants") or {}).get(layer, []) or []
                    if severity:
                        items = [it for it in items if it.get("severity") == severity]
                    view[layer] = items
                return {"ok": True, "bundle": name, "invariants": view, "error": None}
        return _error("not_found", f"no bundle named {name!r}")
