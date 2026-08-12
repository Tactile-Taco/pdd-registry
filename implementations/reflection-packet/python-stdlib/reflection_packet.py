"""reflection-packet implementation (draft bundle candidate).

Mechanical aggregation of annotation layers into a distilled reflection packet
(per session): overview, tension summary, topic flow, case-study candidates,
provenance, stats, and an optional chunk × layer heatmap with baseline-deviation
normalization. Derived-only: no LLM calls, no raw transcript content.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from common import (
    bundle_schema_path,
    fidelity_for,
    read_jsonl,
    sha256_json,
    sha256_text,
    validate_against_schema,
)

PASS_ID = "reflection-packet"
PASS_VERSION = "0.1.0-draft"

LAYER_COLUMNS = ["uncertainty-density", "contention-count", "topic-count"]


def _load_chunk_map(source: str, filename: str, store_dir: str) -> dict:
    cm_path = os.path.join(store_dir, "chunk-store", source, filename + ".chunkmap.json")
    with open(cm_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_turns(source: str, filename: str, store_dir: str) -> list[dict]:
    p = os.path.join(store_dir, "chunk-store", source, filename + ".render.jsonl")
    return list(read_jsonl(p)) if os.path.exists(p) else []


def _heatmap(chunks: list[dict], records: dict[str, list[dict]], fidelity: str,
             baseline: Optional[Any], use_baseline: bool) -> dict:
    rows = [c["chunk_id"] for c in chunks]
    turn_of_chunk: dict[str, set[str]] = {c["chunk_id"]: set(c["turn_ids"]) for c in chunks}
    density_by_chunk: dict[str, float] = {}
    for r in records.get("uncertainty", []):
        cid = r["target"].get("chunk_id")
        if cid and "density_per_1k" in r.get("payload", {}):
            density_by_chunk[cid] = float(r["payload"]["density_per_1k"])
    cont_by_chunk: dict[str, int] = {}
    for r in records.get("contention", []):
        eid = r["target"].get("event_id")
        for cid, turns in turn_of_chunk.items():
            if eid in turns:
                cont_by_chunk[cid] = cont_by_chunk.get(cid, 0) + 1
                break
    topic_by_chunk: dict[str, int] = {}
    for r in records.get("topic", []):
        cid = r["target"].get("chunk_id")
        if cid:
            topic_by_chunk[cid] = topic_by_chunk.get(cid, 0) + 1

    lossy = fidelity == "lossy"
    cells = []
    for cid in rows:
        density = density_by_chunk.get(cid)
        contention = float(cont_by_chunk.get(cid, 0))
        topics = float(topic_by_chunk.get(cid, 0))
        if use_baseline and baseline is not None:
            density = baseline.deviation("any", "uncertainty-density", density) if density is not None else None
        row = []
        for value in (density, contention, topics):
            row.append(None if (lossy or value is None) else round(value, 4))
        cells.append(row)

    text = " | ".join(["chunk"] + LAYER_COLUMNS) + "\n" + "\n".join(
        " | ".join([cid] + [("·" if v is None else str(v)) for v in row])
        for cid, row in zip(rows, cells))
    html = ("<table><tr><th>chunk</th>" + "".join(f"<th>{c}</th>" for c in LAYER_COLUMNS) + "</tr>"
            + "".join(f"<tr><td>{cid}</td>" + "".join(
                f"<td style='background:rgba(200,30,30,{min(1.0, abs(v or 0) * 0.12):.2f})'>{v if v is not None else ''}</td>"
                for v in row) + "</tr>" for cid, row in zip(rows, cells))
            + "</table>")
    return {
        "matrix": {"rows": rows, "columns": list(LAYER_COLUMNS), "cells": cells,
                   "normalization": "baseline-deviation" if use_baseline else "raw"},
        "render": text + "\n\n" + html,
    }


def build(source: str, filename: str, render_id: str,
          layers: Optional[list[str]] = None, include_heatmap: bool = True,
          baselines_ref: Optional[str] = None, store_dir: Optional[str] = None,
          chunk_store: Optional[str] = None, out_dir: Optional[str] = None) -> dict:
    import annotation_store as as_mod

    store = store_dir or os.environ.get("ANNOTATION_STORE", "./annotation-store")
    cs = chunk_store or os.path.join(store, "chunk-store")
    cm = _load_chunk_map(source, filename, store)
    if cm.get("render_id") != render_id:
        raise ValueError(f"render_id mismatch: {cm.get('render_id')} != {render_id}")
    chunks = cm.get("chunks", [])
    turns = _load_turns(source, filename, store)
    fidelity = fidelity_for(source)

    s = as_mod.AnnotationStore(store)
    want = layers if layers is not None else [
        "uncertainty", "contention", "topic", "transition", "topic-flow"]
    records: dict[str, list[dict]] = {}
    for layer in want:
        records[layer] = s.query(source, filename, layer=layer)["records"]

    # provenance: distinct (pass_id, pass_version) across consumed layers
    passes: list[dict] = []
    seen = set()
    for layer in want:
        for r in records[layer]:
            k = (r["pass_id"], r["pass_version"], r["layer"])
            if k not in seen:
                seen.add(k)
                passes.append({"pass_id": r["pass_id"], "pass_version": r["pass_version"],
                               "layer": r["layer"]})
    passes.sort(key=lambda p: (p["pass_id"], p["layer"]))

    # topic flow: prefer the persisted flow record; else derive edges from transitions
    flow_record = next((r for r in records.get("topic-flow", []) if r["kind"] == "flow"), None)
    if flow_record:
        narrative = str(flow_record["payload"].get("narrative", ""))
        edges = list(flow_record["payload"].get("edges", []))
    else:
        narrative = ""
        edges = []
        for r in records.get("transition", []):
            p = r["payload"]
            if p.get("from_topic_id") and p.get("to_topic_id"):
                edges.append({"from_topic_id": p["from_topic_id"],
                              "to_topic_id": p["to_topic_id"],
                              "relation": {"contiguous": "sequential",
                                           "revival": "revival",
                                           "overlap": "overlap",
                                           "nested": "nested"}.get(p.get("type", ""), "sequential")})

    findings = [r for r in records.get("topic-flow", []) if r["kind"] == "finding"]
    tension_summary = [str(f["payload"].get("title", "")) for f in findings
                       if f["payload"].get("kind") == "tension"]
    if not tension_summary and records.get("contention"):
        tension_summary = [f"{len(records['contention'])} contention event(s)"]
    case_study = [{"title": f["payload"].get("title", ""),
                   "rationale": f["payload"].get("rationale", "")}
                  for f in findings if f["payload"].get("kind") == "case-study-candidate"]

    # heatmap with optional baseline deviation
    baseline = None
    use_baseline = bool(baselines_ref)
    if use_baseline:
        from baselines import BaselineStore
        if os.path.exists(baselines_ref):
            baseline = BaselineStore(baselines_ref)
        else:
            use_baseline = False

    packet = {
        "session": {"source": source, "filename": filename,
                    "render_id": render_id, "fidelity_class": fidelity},
        "provenance": {"passes": passes,
                       "baselines_ref": baselines_ref or ""},
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
        "stats": {layer: len(records[layer]) for layer in want},
    }
    if include_heatmap:
        packet["heatmap"] = _heatmap(chunks, records, fidelity, baseline, use_baseline)

    packet_id = f"packet-{source}-{filename}-{sha256_text(render_id)[:8]}"
    response = {
        "packet_id": packet_id,
        "packet": packet,
        "packet_sha256": sha256_json(packet),
    }
    errs = validate_against_schema(response, bundle_schema_path(PASS_ID, "response.schema.json"))
    if errs:
        raise RuntimeError(f"reflection-packet response failed schema: {errs}")

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{source}-{filename}.packet.json"), "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, sort_keys=True, indent=1)
    return response
