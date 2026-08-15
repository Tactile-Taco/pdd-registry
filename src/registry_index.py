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
_FIELD_WEIGHT = {"name": 10, "purpose": 5, "invariant": 3, "capability": 2}

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

    return {
        "name": name,
        "version": protocol.get("version"),
        "status": protocol.get("status"),
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


def load_catalog(bundles_dir: Path) -> list[dict]:
    """Catalog over every pdd-bundles/* directory (sorted, stable order).

    A missing bundles dir yields an empty catalog (v1 /bundles behavior),
    not a 500 for every route.
    """
    if not bundles_dir.exists():
        return []
    out = []
    for d in sorted(bundles_dir.iterdir()):
        if d.is_dir() and (d / "protocol.yaml").exists():
            out.append(load_bundle(d))
    return out


def summary(b: dict) -> dict:
    """The /bundles index shape: {name, version, status, depends_on, provides}."""
    return {k: b.get(k) for k in ("name", "version", "status", "depends_on", "provides")}


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


# ---------------------------------------------------------------------------
# Implementation index — candidate realizations (implementations/<name>[/<variant>])
# Supports both manifest shapes: the registry-native flat form
# (protocol{name,version}, language, runtime, files[]) and the multi-protocol
# nested form (candidate_manifest.implements[] + host{class,affinity} +
# files{protocol:[]} + per-protocol evidence refs). Tolerant: missing fields
# degrade to defaults; unparseable manifests become error entries.

def _impl_evidence_status(impl_dir: Path, ref: str | None,
                          protocol: str, evidence_root: Path | None) -> dict:
    """Per-protocol evidence: prefer the manifest's own ref (validation-results
    relative to the impl dir), else a bundle-keyed evidence dir. Returns
    {ref, result, timestamp} with None when absent."""
    if ref:
        p = impl_dir / ref
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                return {"ref": ref, "result": "unreadable", "timestamp": None}
            return {"ref": ref, "result": data.get("result"),
                    "timestamp": data.get("timestamp")}
        return {"ref": ref, "result": "missing", "timestamp": None}
    if evidence_root is not None and (evidence_root / protocol).exists():
        return {"ref": f"evidence/{protocol}/", "result": "present", "timestamp": None}
    return {"ref": None, "result": None, "timestamp": None}


def _parse_impl(name: str, variant: str, mf: Path,
                evidence_root: Path | None = None) -> dict:
    try:
        data = json.loads(mf.read_text())
    except (json.JSONDecodeError, OSError):
        return {"name": name, "variant": variant, "error": "unparseable manifest"}
    if not isinstance(data, dict):
        return {"name": name, "variant": variant, "error": "manifest not an object"}
    cm = data.get("candidate_manifest")
    if not isinstance(cm, dict):
        cm = data  # registry-native flat shape

    # protocols implemented
    protocols = []
    impls = cm.get("implements")
    if isinstance(impls, list):
        for e in impls:
            if isinstance(e, dict) and e.get("protocol"):
                protocols.append({
                    "protocol": e["protocol"],
                    "version": e.get("version"),
                    "evidence": _impl_evidence_status(
                        mf.parent, e.get("evidence"), e["protocol"], evidence_root),
                })
    elif isinstance(cm.get("protocol"), dict) and cm["protocol"].get("name"):
        p = cm["protocol"]
        protocols.append({
            "protocol": p["name"], "version": p.get("version"),
            "evidence": _impl_evidence_status(mf.parent, None, p["name"], evidence_root),
        })

    # host info
    host = cm.get("host")
    host_class = None
    affinity = {}
    if isinstance(host, dict):
        host_class = host.get("class")
        aff = host.get("affinity")
        if isinstance(aff, dict):
            affinity = aff
    if not host_class:
        host_class = cm.get("language") or "unknown"
    if not affinity:
        aff_runtime = cm.get("runtime")
        if aff_runtime:
            affinity = {"runtime": aff_runtime}

    files = cm.get("files")
    if isinstance(files, list):
        files_count = len(files)
    elif isinstance(files, dict):
        files_count = sum(len(v) for v in files.values() if isinstance(v, list))
    else:
        files_count = 0

    return {
        "name": name,
        "variant": variant,
        "protocols": protocols,
        "host_class": host_class,
        "affinity": affinity,
        "files_count": files_count,
        "assembly_ref": cm.get("assembly_ref"),
        "provides_seams": cm.get("provides_seams") or [],
        "artifact_id": cm.get("artifact_id"),
    }


def load_implementations(impl_root: Path,
                         evidence_root: Path | None = None) -> list[dict]:
    """Every candidate realization under implementations/, both layouts:
    implementations/<name>/candidate-manifest.json and
    implementations/<name>/<variant>/candidate-manifest.json. Stable order."""
    out = []
    if not impl_root.is_dir():
        return out
    for d in sorted(p for p in impl_root.iterdir() if p.is_dir()):
        found = []
        m = d / "candidate-manifest.json"
        if m.exists():
            found.append((d.name, ".", m))
        for v in sorted(p for p in d.iterdir() if p.is_dir()):
            vm = v / "candidate-manifest.json"
            if vm.exists():
                found.append((d.name, v.name, vm))
        for name, variant, mf in found:
            out.append(_parse_impl(name, variant, mf, evidence_root))
    return out


def impl_matches(e: dict, protocol: str | None = None,
                 host_class: str | None = None,
                 affinity: str | None = None,
                 evidence: str | None = None) -> bool:
    """Filter predicate for the implementation picker."""
    if "error" in e:
        return False
    if protocol:
        names = {p.get("protocol") for p in e["protocols"]}
        if protocol.lower() not in {n.lower() for n in names}:
            return False
    if host_class:
        if e["host_class"].lower() != host_class.lower():
            return False
    if affinity:
        hay = " ".join(str(v).lower() for v in e["affinity"].values())
        if affinity.lower() not in hay:
            return False
    if evidence == "pass":
        results = [p["evidence"].get("result") for p in e["protocols"]
                   if p["evidence"].get("result")]
        if not results or any(r != "pass" for r in results):
            return False
    elif evidence == "any":
        if not any(p["evidence"].get("result") for p in e["protocols"]):
            return False
    return True


def impl_rank_key(e: dict) -> tuple:
    """Sort: protocol coverage desc, evidence passes desc, name asc (stable).
    Higher = better. Error entries sink to the bottom."""
    if "error" in e:
        return (0, 0, "")
    passes = sum(1 for p in e["protocols"] if p["evidence"].get("result") == "pass")
    return (len(e["protocols"]), passes, e["name"])


def filter_implementations(entries: list[dict], protocol: str | None = None,
                           host_class: str | None = None,
                           affinity: str | None = None,
                           evidence: str | None = None) -> list[dict]:
    matched = [e for e in entries
               if impl_matches(e, protocol, host_class, affinity, evidence)]
    return sorted(matched, key=impl_rank_key, reverse=True)
