"""topic-flow-review — attested candidate core (pure, self-contained).

Consumes topic/transition/contention records (already queried), derives the
intra-session flow graph mechanically from transitions, and produces findings
via the duck-typed router. Finding refs are validated against the real
annotation inventory; invalid refs are dropped; a mechanical fallback keeps
findings grounded when the router returns nothing usable.
"""

import json
import re
from typing import Dict, List, Optional

from _hash import sha256_json

ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

PASS_ID = "topic-flow-review"
PASS_VERSION = "0.1.0-draft"

_TRANSITION_TO_RELATION = {
    "contiguous": "sequential", "revival": "revival",
    "overlap": "overlap", "nested": "nested",
}
_KINDS = ("tension", "skill-improvement-candidate", "case-study-candidate", "observation")

_SYSTEM = (
    "You review the topic flow of an AI session transcript. Return ONLY a JSON "
    'object: {"narrative": "<2-4 sentence flow narrative>", "findings": ['
    '{kind: tension|skill-improvement-candidate|case-study-candidate|observation, '
    "title, rationale, supporting_refs: [{layer, annotation_id}]}]}. "
    "Every supporting_ref must be one of the annotation ids provided to you; "
    "cite the ids that actually back the finding."
)


def derive_flow(transitions: List[dict]) -> Dict:
    seen = set()
    edges = []
    for tr in transitions:
        p = tr.get("payload", {})
        rel = _TRANSITION_TO_RELATION.get(p.get("type", ""), "sequential")
        e = (p.get("from_topic_id"), p.get("to_topic_id"), rel)
        if e[0] and e[1] and e not in seen:
            seen.add(e)
            edges.append({"from_topic_id": e[0], "to_topic_id": e[1], "relation": rel})
    return {"narrative": "", "intra_session_edges": edges}


def _mechanical_findings(contention: List[dict], topics: List[dict]) -> List[dict]:
    findings = []
    if contention:
        findings.append({
            "finding_id": _aid("mech-tension-00000001"),
            "kind": "tension",
            "title": f"{len(contention)} contention event(s) in this session",
            "rationale": "Mechanical fallback: user-turn contention markers detected.",
            "supporting_refs": [{"layer": "contention", "annotation_id": r["annotation_id"]}
                                for r in contention[:5]],
        })
    if topics:
        findings.append({
            "finding_id": _aid("mech-casestudy-00000001"),
            "kind": "case-study-candidate",
            "title": f"Session spans {len(topics)} topic annotations",
            "rationale": "Mechanical fallback: multi-topic session with structured topic layer.",
            "supporting_refs": [{"layer": "topic", "annotation_id": r["annotation_id"]}
                                for r in topics[:3]],
        })
    return findings


def run(source: str, filename: str, chunk_map: dict,
        topics: List[dict], transitions: List[dict], contention: List[dict],
        router=None, report_depth: str = "full") -> dict:
    if source not in ("reasonix", "omp", "claude", "codex", "kimi", "hermes"):
        raise ValueError("invalid_request: unknown source")
    flow = derive_flow(transitions)

    inventory: Dict[str, set] = {}
    for rec in topics + transitions + contention:
        inventory.setdefault(rec.get("layer", ""), set()).add(rec["annotation_id"])

    tokens_in = tokens_out = 0
    findings: List[dict] = []
    narrative = ""
    if router is not None:
        prompt = (
            f"Session {source}/{filename} (depth {report_depth}).\n"
            f"TOPIC RECORDS: {json.dumps([{'id': t['annotation_id'], 'label': t.get('payload', {}).get('label'), 'topic_id': t.get('payload', {}).get('topic_id')} for t in topics], ensure_ascii=False)}\n"
            f"TRANSITION RECORDS: {json.dumps([{'id': t['annotation_id'], 'from': t.get('payload', {}).get('from_topic_id'), 'to': t.get('payload', {}).get('to_topic_id'), 'type': t.get('payload', {}).get('type')} for t in transitions], ensure_ascii=False)}\n"
            f"CONTENTION RECORDS: {json.dumps([{'id': c['annotation_id'], 'markers': c.get('payload', {}).get('markers')} for c in contention], ensure_ascii=False)}\n"
            f"DERIVED FLOW EDGES: {json.dumps(flow['intra_session_edges'], ensure_ascii=False)}\n"
            "Produce the narrative and findings; cite annotation ids from the records above."
        )
        out = router.complete_json(prompt, system=_SYSTEM)
        tokens_in += int(getattr(router, "last_usage", {}).get("tokens_in", 0))
        tokens_out += int(getattr(router, "last_usage", {}).get("tokens_out", 0))
        narrative = str((out.get("narrative") or "").strip())
        for f in (out.get("findings") or []):
            kind = f.get("kind")
            if kind not in _KINDS:
                continue
            refs = []
            for ref in f.get("supporting_refs") or []:
                layer = str(ref.get("layer", ""))
                aid = str(ref.get("annotation_id", ""))
                if layer in inventory and aid in inventory[layer]:
                    refs.append({"layer": layer, "annotation_id": aid})
            if not refs:
                continue
            findings.append({
                "finding_id": _aid(f"f-{len(findings)}-00000001"),
                "kind": kind,
                "title": str(f.get("title", "")).strip() or "untitled finding",
                "rationale": str(f.get("rationale", "")).strip(),
                "supporting_refs": refs,
            })
    if not findings:
        findings = _mechanical_findings(contention, topics)
    flow["narrative"] = narrative or (
        f"Mechanical flow: {len(topics)} topics, {len(transitions)} transitions, "
        f"{len(contention)} contention events across {len(chunk_map.get('chunks', []))} chunks.")

    first_chunk = (chunk_map.get("chunks") or [{"chunk_id": "c0"}])[0]["chunk_id"]
    records = [{
        "annotation_id": f["finding_id"],
        "layer": "topic-flow", "kind": "finding",
        "target": {"source": source, "filename": filename, "chunk_id": first_chunk},
        "revision": 1,
        "payload": {"kind": f["kind"], "title": f["title"],
                    "rationale": f["rationale"], "supporting_refs": f["supporting_refs"]},
        "created_at": "2026-08-12T00:00:00Z",
    } for f in findings]
    records.append({
        "annotation_id": _aid("flow-00000001"),
        "layer": "topic-flow", "kind": "flow",
        "target": {"source": source, "filename": filename, "chunk_id": first_chunk},
        "revision": 1,
        "payload": {"narrative": flow["narrative"], "edges": flow["intra_session_edges"]},
        "created_at": "2026-08-12T00:00:00Z",
    })

    return {
        "pass_id": PASS_ID, "pass_version": PASS_VERSION,
        "flow": flow, "findings": findings, "records": records,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "records_sha256": sha256_json(records),
    }


def _aid(slug: str) -> str:
    s = re.sub(r"[^a-z0-9-]", "-", slug).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if len(s) < 8:
        s = s + "-" + re.sub(r"[^a-f0-9]", "", sha256_json({"s": slug}))[:8]
    return s[:64]
