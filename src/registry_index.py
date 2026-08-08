"""registry_index — catalog + search index over pdd-bundles/ (stdlib-only).

Shared by scripts/pdd.py (CLI: `pdd index`, `pdd search`) and src/server.py
(HTTP: /search, /bundles, /bundles/{name}/invariants|capabilities|ledger) so
the CLI and the service serve the SAME index — the v2 surface of
docs/service-features-v2.md.

Fail-closed: YAML parsing requires pyyaml (pinned in the Dockerfile). If it is
missing, index operations raise a clear error instead of fabricating data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

LAYERS = ("structural", "behavioral", "operational")
INVARIANT_KEYS = {
    "structural": "structural_invariants",
    "behavioral": "behavioral_invariants",
    "operational": "operational_invariants",
}
SEVERITIES = ("must", "should")

# Relevance weights for search ranking (field importance, not severity).
_FIELD_WEIGHT = {"name": 10, "purpose": 5, "invariant": 3, "capability": 2, "tag": 2}

_WORD = re.compile(r"[a-z0-9_]+")


def _require_yaml() -> None:
    if yaml is None:
        raise RuntimeError(
            "pyyaml is required for registry index operations "
            "(pinned in the Dockerfile: pyyaml==6.0.3)")


def _load_yaml(path: Path) -> dict:
    _require_yaml()
    return yaml.safe_load(path.read_text()) or {}


def load_bundle(bundle_dir: Path) -> dict:
    """Parse one bundle directory into a catalog entry (or an error entry)."""
    name = bundle_dir.name
    proto_path = bundle_dir / "protocol.yaml"
    if not proto_path.exists():
        return {"name": name, "error": f"missing {proto_path.name}"}
    try:
        proto = _load_yaml(proto_path)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "error": f"unparseable {proto_path.name}: {exc}"}
    protocol = proto.get("protocol") or proto

    invariants = {}
    for layer in LAYERS:
        inv_path = bundle_dir / "invariants" / f"{layer}.yaml"
        items = []
        if inv_path.exists():
            try:
                items = _load_yaml(inv_path).get(INVARIANT_KEYS[layer], []) or []
            except Exception as exc:  # noqa: BLE001
                return {"name": name, "error": f"unparseable {inv_path.name}: {exc}"}
        invariants[layer] = [
            {"id": it.get("id"), "statement": it.get("statement"),
             "severity": it.get("severity", "must"), "validation": it.get("validation", [])}
            for it in items if isinstance(it, dict) and it.get("id")
        ]

    capabilities = {}
    cap_path = bundle_dir / "capability-manifest.yaml"
    if cap_path.exists():
        try:
            capabilities = _load_yaml(cap_path).get("capabilities", {}) or {}
        except Exception as exc:  # noqa: BLE001
            return {"name": name, "error": f"unparseable {cap_path.name}: {exc}"}
        if not isinstance(capabilities, dict):
            # Non-dict manifest (e.g. a list) would crash search/index later.
            capabilities = {}

    # Normalize shapes so downstream consumers never see the raw YAML types:
    # a string depends_on would turn the /bundles?depends_on= filter into
    # substring matching instead of exact membership.
    depends_on = proto.get("depends_on", []) or []
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    provides = proto.get("provides", {}) or {}
    if not isinstance(provides, dict):
        provides = {}
    # v1.1 catalog metadata (S-004/S-005): namespace is a kebab-case owner
    # slug; tags a kebab-case list. Defensive normalization mirrors
    # depends_on: a string tags value becomes a single-element list, anything
    # else degenerates to [] so filters/search never crash on raw types.
    namespace = proto.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        namespace = None
    tags = proto.get("tags")
    if isinstance(tags, str):
        tags = [tags]
    elif not isinstance(tags, list):
        tags = []
    else:
        tags = [t for t in tags if isinstance(t, str)]

    return {
        "name": name,
        "version": protocol.get("version"),
        "status": protocol.get("status"),
        "namespace": namespace,
        "tags": tags,
        # Display address namespace/name (Docker-Hub owner / npm scope style);
        # falls back to bare name for bundles without a namespace.
        "address": f"{namespace}/{name}" if namespace else name,
        # purpose/boundary/depends_on/provides are top-level keys in
        # protocol.yaml, siblings of `protocol:` (which holds name/version/status).
        "purpose": proto.get("purpose"),
        "boundary": proto.get("boundary", {}) or {},
        "depends_on": depends_on,
        "provides": provides,
        "invariants": invariants,
        "capabilities": capabilities,
        "_dir": str(bundle_dir),
    }


def _mark_duplicate_addresses(entries: list[dict]) -> None:
    """S-004: flag every entry whose (namespace, name) address is shared with
    another entry (in place, fail-closed). The flat pdd-bundles layout already
    guarantees unique directory names, so today this guards the catalog-builder
    boundary — e.g. a future subdirectory/alias layout must not reintroduce
    duplicate addresses silently."""
    from collections import Counter
    counts = Counter((b.get("namespace"), b["name"]) for b in entries if "error" not in b)
    for b in entries:
        if "error" in b:
            continue
        key = (b.get("namespace"), b["name"])
        if counts[key] > 1:
            b["error"] = f"duplicate catalog address {key[0]}/{key[1]} ({counts[key]} entries)"


def load_catalog(bundles_dir: Path) -> list[dict]:
    """Catalog over every pdd-bundles/* directory (sorted, stable order).

    A missing bundles dir yields an empty catalog (v1 /bundles behavior),
    not a 500 for every route.

    S-004: catalog entries whose (namespace, name) address collides with
    another entry are marked broken (error entry) — fail-closed, so a
    duplicate address is visible to operators and never silently served.
    """
    if not bundles_dir.exists():
        return []
    out = []
    for d in sorted(bundles_dir.iterdir()):
        if d.is_dir() and (d / "protocol.yaml").exists():
            out.append(load_bundle(d))
    _mark_duplicate_addresses(out)
    return out


def summary(b: dict) -> dict:
    """The /bundles index shape: {name, namespace, tags, address, version,
    status, depends_on, provides}."""
    return {k: b.get(k) for k in ("name", "namespace", "tags", "address",
                                  "version", "status", "depends_on", "provides")}


def invariants_view(b: dict, severity: str | None = None) -> dict:
    """Structured S/B/O invariant view; optional severity filter."""
    view = {}
    for layer in LAYERS:
        items = b.get("invariants", {}).get(layer, [])
        if severity:
            items = [it for it in items if it.get("severity") == severity]
        view[layer] = items
    return view


def _index_entries(b: dict) -> list[tuple[str, str, str, str, int]]:
    """Searchable entries: (bundle, layer/field, id, text, weight)."""
    entries = []
    name = b.get("name")
    if name:
        entries.append((name, "bundle", "name", name, _FIELD_WEIGHT["name"]))
    purpose = b.get("purpose")
    if purpose:
        entries.append((name, "bundle", "purpose", purpose, _FIELD_WEIGHT["purpose"]))
    for layer in LAYERS:
        for it in b.get("invariants", {}).get(layer, []):
            text = f"{it.get('id')}: {it.get('statement')}"
            entries.append((name, layer, it.get("id"), text, _FIELD_WEIGHT["invariant"]))
    for key in (b.get("capabilities") or {}):
        entries.append((name, "capabilities", key, key, _FIELD_WEIGHT["capability"]))
    for t in b.get("tags") or []:
        entries.append((name, "tags", t, t, _FIELD_WEIGHT["tag"]))
    return entries


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def search(catalog: list[dict], query: str, limit: int = 20) -> list[dict]:
    """Ranked search over the catalog. All query tokens must appear (AND).

    Matching is substring-based on lowercased text, so `pdd search idempotent`
    surfaces user-registry's purpose AND B-001's statement. Results are ranked
    by field weight, then bundle name / layer / id for a stable order.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []
    results = []
    for b in catalog:
        if "error" in b:
            continue
        for bundle, layer, eid, text, weight in _index_entries(b):
            lowered = text.lower()
            if all(tok in lowered for tok in tokens):
                results.append({
                    "bundle": bundle, "layer": layer, "id": eid,
                    "text": text, "score": weight,
                })
    results.sort(key=lambda r: (-r["score"], r["bundle"], r["layer"], r["id"]))
    return results[:limit]


def catalog_json(catalog: list[dict]) -> dict:
    """The `pdd index` output: bundles + a per-bundle invariant count."""
    bundles = []
    for b in catalog:
        if "error" in b:
            bundles.append(b)
            continue
        bundles.append({
            "name": b["name"], "version": b["version"], "status": b["status"],
            "namespace": b.get("namespace"), "tags": b.get("tags") or [],
            "address": b.get("address"),
            "depends_on": b["depends_on"], "provides": b["provides"],
            "invariant_count": {layer: len(b["invariants"][layer]) for layer in LAYERS},
            "capability_keys": sorted((b.get("capabilities") or {}).keys()),
        })
    return {"bundles": bundles, "count": len(bundles)}


def ledger_view(evidence_root: Path, name: str, limit: int | None = None) -> dict:
    """Last N blocks of evidence/<name>/runtime-ledger.jsonl (raw, read-only).

    Verification of the blocks is NOT done here — the HTTP layer runs the real
    ledger verification (fail-closed) and reports it as `verified`. This view
    returns exactly what is on disk, like `pdd evidence verify` reads it.
    """
    # Defense in depth: this shared function must never escape the evidence
    # root. The HTTP layer already constrains names to real bundle dirs.
    if Path(name).name != name or name in (".", ".."):
        return {"bundle": name, "error": "invalid bundle name", "count": 0, "blocks": []}
    ledger = evidence_root / name / "runtime-ledger.jsonl"
    if not ledger.exists():
        return {"bundle": name, "error": "no runtime-ledger.jsonl", "count": 0, "blocks": []}
    blocks = []
    for line in ledger.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            blocks.append(json.loads(line))
        except json.JSONDecodeError:
            blocks.append({"error": "unparseable ledger line"})
    total = len(blocks)
    if limit is not None:
        if limit <= 0:
            blocks = []
        elif total > limit:
            blocks = blocks[-limit:]
    return {"bundle": name, "count": total, "blocks": blocks}
