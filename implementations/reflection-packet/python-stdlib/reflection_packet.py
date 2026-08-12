"""reflection-packet — attested candidate core (pure, self-contained).

Mechanical aggregation of annotation layers into a distilled reflection packet:
overview, provenance, tension summary, topic flow, case-study candidates,
stats, and a chunk × layer heatmap (raw or baseline-deviation, null cells for
lossy fidelity). Derived-only: no LLM, no I/O, no raw transcript content.
"""

import json
from typing import Dict, List, Optional

from _hash import sha256_json, sha256_text

ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

PASS_ID = "reflection-packet"
PASS_VERSION = "0.1.0-draft"

LAYER_COLUMNS = ["uncertainty-density", "contention-count", "topic-count"]
_FIDELITY = {"reasonix": "full", "omp": "full", "claude": "lossy",
             "codex": "lossy", "kimi": "full", "hermes": "full"}
_REL = {"contiguous": "sequential", "revival": "revival",
        "overlap": "overlap", "nested": "nested"}


def _heatmap(chunks: List[dict], records: Dict[str, List[dict]], fidelity: str,
             baseline: Optional[Dict], use_baseline: bool) -> dict:
    rows = [c["chunk_id"] for c in chunks]
    turn_of_chunk = {c["chunk_id"]: set(c.get("turn_ids", [])) for c in chunks}
    density_by_chunk: Dict[str, float] = {}
    for r in records.get("uncertainty", []):
        cid = r.get("target", {}).get("chunk_id")
        if cid and "density_per_1k" in r.get("payload", {}):
            density_by_chunk[cid] = float(r["payload"]["density_per_1k"])
    cont_by_chunk: Dict[str, int] = {}
    for r in records.get("contention", []):
        eid = r.get("target", {}).get("event_id")
        for cid, turns in turn_of_chunk.items():
            if eid in turns:
                cont_by_chunk[cid] = cont_by_chunk.get(cid, 0) + 1
                break
    topic_by_chunk: Dict[str, int] = {}
    for r in records.get("topic", []):
        cid = r.get("target", {}).get("chunk_id")
        if cid:
            topic_by_chunk[cid] = topic_by_chunk.get(cid, 0) + 1

    lossy = fidelity == "lossy"
    cells = []
    for cid in rows:
        density = density_by_chunk.get(cid)
        if use_baseline and baseline is not None and density is not None:
            mean = baseline.get("mean")
            std = baseline.get("std")
            if std:
                density = (density - mean) / std
            else:
                density = None
        values = (density, float(cont_by_chunk.get(cid, 0)),
                  float(topic_by_chunk.get(cid, 0)))
        cells.append([None if (lossy or v is None) else round(v, 4) for v in values])

    text = " | ".join(["chunk"] + LAYER_COLUMNS) + "\n" + "\n".join(
        " | ".join([cid] + [("·" if v is None else str(v)) for v in row])
        for cid, row in zip(rows, cells))
    html = ("<table><tr><th>chunk</th>" + "".join(f"<th>{c}</th>" for c in LAYER_COLUMNS) + "</tr>"
            + "".join(f"<tr><td>{cid}</td>" + "".join(
                f"<td>{v if v is not None else ''}</td>" for v in row) + "</tr>"
                for cid, row in zip(rows, cells)) + "</table>")
    return {
        "matrix": {"rows": rows, "columns": list(LAYER_COLUMNS), "cells": cells,
                   "normalization": "baseline-deviation" if use_baseline else "raw"},
        "render": text + "\n\n" + html,
    }


def build(source: str, filename: str, render_id: str, chunk_map: dict,
          turns: List[dict], records_by_layer: Dict[str, List[dict]],
          layers: Optional[List[str]] = None, include_heatmap: bool = True,
          baselines_ref: Optional[str] = None,
          baseline_stats: Optional[Dict] = None) -> dict:
    if source not in _FIDELITY:
        raise ValueError("invalid_request: unknown source")
    chunks = chunk_map.get("chunks", [])
    fidelity = _FIDELITY[source]
    want = layers if layers is not None else [
        "uncertainty", "contention", "topic", "transition", "topic-flow"]

    passes: List[dict] = []
    seen = set()
    for layer in want:
        for r in records_by_layer.get(layer, []):
            k = (r.get("pass_id"), r.get("pass_version"), r.get("layer"))
            if k not in seen:
                seen.add(k)
                passes.append({"pass_id": k[0], "pass_version": k[1], "layer": k[2]})
    passes.sort(key=lambda p: (p["pass_id"], p["layer"]))

    flow_record = next((r for r in records_by_layer.get("topic-flow", [])
                        if r.get("kind") == "flow"), None)
    if flow_record:
        narrative = str(flow_record.get("payload", {}).get("narrative", ""))
        edges = list(flow_record.get("payload", {}).get("edges", []))
    else:
        narrative = ""
        edges = []
        for r in records_by_layer.get("transition", []):
            p = r.get("payload", {})
            if p.get("from_topic_id") and p.get("to_topic_id"):
                edges.append({"from_topic_id": p["from_topic_id"],
                              "to_topic_id": p["to_topic_id"],
                              "relation": _REL.get(p.get("type", ""), "sequential")})

    findings = [r for r in records_by_layer.get("topic-flow", [])
                if r.get("kind") == "finding"]
    tension_summary = [str(f.get("payload", {}).get("title", "")) for f in findings
                       if f.get("payload", {}).get("kind") == "tension"]
    if not tension_summary and records_by_layer.get("contention"):
        tension_summary = [f"{len(records_by_layer['contention'])} contention event(s)"]
    case_study = [{"title": f.get("payload", {}).get("title", ""),
                   "rationale": f.get("payload", {}).get("rationale", "")}
                  for f in findings if f.get("payload", {}).get("kind") == "case-study-candidate"]

    use_baseline = bool(baselines_ref and baseline_stats)
    packet = {
        "session": {"source": source, "filename": filename,
                    "render_id": render_id, "fidelity_class": fidelity},
        "provenance": {"passes": passes, "baselines_ref": baselines_ref or ""},
        "overview": {
            "turn_count": len(turns),
            "chunk_count": len(chunks),
            "fidelity_note": ("lossy harness: compaction may have rewritten history"
                              if fidelity == "lossy"
                              else "full-fidelity append-only transcript"),
        },
        "tension_summary": tension_summary,
        "topic_flow": {"narrative": narrative, "edges": edges},
        "case_study_candidates": case_study,
        "baseline_refs": [baselines_ref] if baselines_ref else [],
        "stats": {layer: len(records_by_layer.get(layer, [])) for layer in want},
    }
    if include_heatmap:
        packet["heatmap"] = _heatmap(chunks, records_by_layer, fidelity,
                                     baseline_stats, use_baseline)

    response = {
        "packet_id": f"packet-{source}-{filename}-{sha256_text(render_id)[:8]}",
        "packet": packet,
        "packet_sha256": sha256_json(packet),
    }
    return response
