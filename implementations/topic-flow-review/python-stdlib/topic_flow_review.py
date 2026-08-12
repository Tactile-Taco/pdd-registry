"""topic-flow-review implementation (draft bundle candidate).

Consumes topic/transition/contention layers from the annotation store, derives
the intra-session flow graph mechanically from transitions, and produces
findings via the injected router (findings must reference existing annotation
ids — invalid refs are dropped, with a mechanical fallback so the flow report
always has grounded findings).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from common import (
    bundle_schema_path,
    read_jsonl,
    sha256_json,
    validate_against_schema,
)
from router import ModelRouter, RouterError

PASS_ID = "topic-flow-review"
PASS_VERSION = "0.1.0-draft"

TRANSITION_TO_RELATION = {
    "contiguous": "sequential",
    "revival": "revival",
    "overlap": "overlap",
    "nested": "nested",
}

_SYSTEM = (
    "You review the topic flow of an AI session transcript. Return ONLY a JSON "
    'object: {"narrative": "<2-4 sentence flow narrative>", "findings": ['
    '{kind: tension|skill-improvement-candidate|case-study-candidate|observation, '
    "title, rationale, supporting_refs: [{layer, annotation_id}]}]}. "
    "Every supporting_ref must be one of the annotation ids provided to you; "
    "cite the ids that actually back the finding."
)


def _load_chunk_map(source: str, filename: str, store_dir: str) -> dict:
    cm_path = os.path.join(store_dir, "chunk-store", source, filename + ".chunkmap.json")
    with open(cm_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _derive_flow(transitions: list[dict]) -> dict:
    """Mechanical derivation from transition records (flow-derived-only)."""
    seen = set()
    edges = []
    for tr in transitions:
        rel = TRANSITION_TO_RELATION.get(tr["payload"].get("type", ""), "sequential")
        e = (tr["payload"].get("from_topic_id"), tr["payload"].get("to_topic_id"), rel)
        if e not in seen:
            seen.add(e)
            edges.append({"from_topic_id": e[0], "to_topic_id": e[1], "relation": e[2]})
    return {"narrative": "", "intra_session_edges": edges}


def _mechanical_findings(contention: list[dict], topics: list[dict]) -> list[dict]:
    """Grounded fallback findings when the router returns nothing usable."""
    findings = []
    if contention:
        refs = [{"layer": "contention", "annotation_id": r["annotation_id"]}
                for r in contention[:5]]
        findings.append({
            "finding_id": _aid("mech-tension-00000001"),
            "kind": "tension",
            "title": f"{len(contention)} contention event(s) in this session",
            "rationale": "Mechanical fallback: user-turn contention markers detected.",
            "supporting_refs": refs,
        })
    if topics:
        refs = [{"layer": "topic", "annotation_id": r["annotation_id"]}
                for r in topics[:3]]
        findings.append({
            "finding_id": _aid("mech-casestudy-00000001"),
            "kind": "case-study-candidate",
            "title": f"Session spans {len(topics)} topic annotations",
            "rationale": "Mechanical fallback: multi-topic session with structured topic layer.",
            "supporting_refs": refs,
        })
    return findings


def run(source: str, filename: str, render_id: str,
        input_layers: Optional[list[str]] = None, report_depth: str = "full",
        router: Optional[Any] = None, store_dir: Optional[str] = None,
        chunk_store: Optional[str] = None) -> dict:
    import annotation_store as as_mod

    store = store_dir or os.environ.get("ANNOTATION_STORE", "./annotation-store")
    cs = chunk_store or os.path.join(store, "chunk-store")
    cm = _load_chunk_map(source, filename, store)
    if cm.get("render_id") != render_id:
        raise ValueError(f"render_id mismatch: {cm.get('render_id')} != {render_id}")

    s = as_mod.AnnotationStore(store)
    layers = input_layers if input_layers is not None else ["topic", "transition"]
    topics = s.query(source, filename, layer="topic")["records"]
    transitions = s.query(source, filename, layer="transition")["records"]
    contention = s.query(source, filename, layer="contention")["records"] if "contention" in layers else []

    flow = _derive_flow(transitions)

    # inventory of valid refs (layer -> set of annotation ids)
    inventory: dict[str, set[str]] = {}
    for rec in topics + transitions + contention:
        inventory.setdefault(rec["layer"], set()).add(rec["annotation_id"])

    tokens_in = tokens_out = 0
    findings: list[dict] = []
    r = router or ModelRouter()
    prompt = (
        f"Session {source}/{filename} (render {render_id}, depth {report_depth}).\n"
        f"TOPIC RECORDS: {json.dumps([{ 'id': t['annotation_id'], 'label': t['payload'].get('label'), 'topic_id': t['payload'].get('topic_id')} for t in topics], ensure_ascii=False)}\n"
        f"TRANSITION RECORDS: {json.dumps([{ 'id': t['annotation_id'], 'from': t['payload'].get('from_topic_id'), 'to': t['payload'].get('to_topic_id'), 'type': t['payload'].get('type')} for t in transitions], ensure_ascii=False)}\n"
        f"CONTENTION RECORDS: {json.dumps([{ 'id': c['annotation_id'], 'markers': c['payload'].get('markers')} for c in contention], ensure_ascii=False)}\n"
        f"DERIVED FLOW EDGES: {json.dumps(flow['intra_session_edges'], ensure_ascii=False)}\n"
        "Produce the narrative and findings; cite annotation ids from the records above."
    )
    try:
        out = r.complete_json(prompt, system=_SYSTEM)
    except RouterError:
        out = {}
    tokens_in += _usage(r, "in")
    tokens_out += _usage(r, "out")

    for f in (out.get("findings") or []):
        kind = f.get("kind")
        if kind not in ("tension", "skill-improvement-candidate", "case-study-candidate", "observation"):
            continue
        refs = []
        for ref in f.get("supporting_refs") or []:
            layer = ref.get("layer", "")
            aid = ref.get("annotation_id", "")
            if layer in inventory and aid in inventory[layer]:
                refs.append({"layer": layer, "annotation_id": aid})
        if not refs:
            continue  # finding-grounded: refs must actually exist
        findings.append({
            "finding_id": _aid(f"f-{len(findings)}-00000001"),
            "kind": kind,
            "title": str(f.get("title", "")).strip() or "untitled finding",
            "rationale": str(f.get("rationale", "")).strip(),
            "supporting_refs": refs,
        })
    if not findings:
        findings = _mechanical_findings(contention, topics)
    flow["narrative"] = str((out.get("narrative") or "").strip()) or (
        f"Mechanical flow: {len(topics)} topics, {len(transitions)} transitions, "
        f"{len(contention)} contention events across {len(cm.get('chunks', []))} chunks.")

    # findings target the first chunk (envelope requires event_id or chunk_id)
    first_chunk = (cm.get("chunks") or [{"chunk_id": "c0"}])[0]["chunk_id"]
    records = [{
        "annotation_id": f["finding_id"],
        "layer": "topic-flow",
        "kind": "finding",
        "target": {"source": source, "filename": filename, "chunk_id": first_chunk},
        "revision": 1,
        "payload": {"kind": f["kind"], "title": f["title"], "rationale": f["rationale"],
                    "supporting_refs": f["supporting_refs"]},
        "created_at": "2026-08-12T00:00:00Z",
    } for f in findings]
    # persist the flow itself so downstream consumers (reflection-packet) can
    # read narrative + edges without re-deriving or re-calling a model
    records.append({
        "annotation_id": _aid("flow-00000001"),
        "layer": "topic-flow",
        "kind": "flow",
        "target": {"source": source, "filename": filename, "chunk_id": first_chunk},
        "revision": 1,
        "payload": {"narrative": flow["narrative"], "edges": flow["intra_session_edges"]},
        "created_at": "2026-08-12T00:00:00Z",
    })

    response = {
        "pass_id": PASS_ID,
        "pass_version": PASS_VERSION,
        "flow": flow,
        "findings": findings,
        "records": records,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "records_sha256": sha256_json(records),
    }
    errs = validate_against_schema(response, bundle_schema_path(PASS_ID, "response.schema.json"))
    if errs:
        raise RuntimeError(f"topic-flow-review response failed schema: {errs}")

    s.append(PASS_ID, PASS_VERSION, records)
    return response


def _usage(router: Any, side: str) -> int:
    last = getattr(router, "last_usage", None)
    if isinstance(last, dict):
        return int(last.get(f"tokens_{side}", 0))
    return 0


def _aid(slug: str) -> str:
    import hashlib
    s = re.sub(r"[^a-z0-9-]", "-", slug).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if len(s) < 8:
        s = s + "-" + hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    return s[:64]
