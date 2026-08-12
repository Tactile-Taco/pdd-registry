"""topic-transition-pass implementation (draft bundle candidate).

LLM-backed topic + transition annotation. The ONLY network surface is the
injected router (ModelRouter or StubRouter in tests). Label consistency via
existing_labels; deterministic topic-id assignment in first-appearance order.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from common import (
    bundle_schema_path,
    read_jsonl,
    sha256_json,
    validate_against_schema,
)
from router import ModelRouter, RouterError

PASS_ID = "topic-transition-pass"
PASS_VERSION = "0.1.0-draft"

MAX_PROMPT_CHARS = 100_000

_SYSTEM = (
    "You annotate AI-session transcripts. Return ONLY a JSON object with "
    '"topics" (array of {label, intensity 0..1, quotes: [short verbatim text '
    'excerpts from the chunk]}) and "transitions" (array of {from_label, '
    'to_label, type: one of contiguous|revival|overlap|nested, signal_text}). '
    'Reuse existing labels verbatim when they fit; introduce new labels only '
    "when needed. Transitions link the topics of consecutive chunks."
)


def _load_chunk_store(source: str, filename: str, chunk_store: str) -> tuple[list[dict], list[dict], str]:
    turns_path = os.path.join(chunk_store, source, filename + ".render.jsonl")
    cm_path = os.path.join(chunk_store, source, filename + ".chunkmap.json")
    if not os.path.exists(turns_path) or not os.path.exists(cm_path):
        raise FileNotFoundError(f"render/chunk map not materialized for {source}/{filename}")
    with open(cm_path, "r", encoding="utf-8") as f:
        chunkmap = json.load(f)
    return list(read_jsonl(turns_path)), chunkmap["chunks"], chunkmap.get("render_id", "")


def _chunk_text(chunks: list[dict], turns_by_id: dict[str, dict], c: dict) -> str:
    parts = []
    for tid in c["turn_ids"]:
        t = turns_by_id[tid]
        parts.append(f"[{t['role']}] {t.get('content', '')}")
    return "\n".join(parts)


def _topic_id_for(label: str, assigned: dict[str, str]) -> str:
    key = label.casefold()
    if key in assigned:
        return assigned[key]
    tid = f"t{len(assigned)}"
    assigned[key] = tid
    return tid


def run(source: str, filename: str, render_id: str, chunk_ids: Optional[list[str]] = None,
        existing_labels: Optional[list[str]] = None, emit_layers: Optional[list[str]] = None,
        router: Optional[Any] = None, store_dir: Optional[str] = None,
        chunk_store: Optional[str] = None) -> dict:
    import annotation_store as as_mod

    store = store_dir or os.environ.get("ANNOTATION_STORE", "./annotation-store")
    cs = chunk_store or os.path.join(store, "chunk-store")
    turns, chunks, actual_rid = _load_chunk_store(source, filename, cs)
    if actual_rid != render_id:
        raise ValueError(f"render_id mismatch: {actual_rid} != {render_id}")
    turns_by_id = {t["event_id"]: t for t in turns}
    want = chunk_ids if chunk_ids is not None else [c["chunk_id"] for c in chunks]
    wanted = [c for c in chunks if c["chunk_id"] in want]
    if len(wanted) != len(want):
        raise ValueError("requested chunk_ids not all present in chunk map")

    r = router or ModelRouter()
    existing = [l for l in (existing_labels or [])]

    assigned: dict[str, str] = {}  # casefold label -> topic_id
    topics_out: list[dict] = []
    transitions_out: list[dict] = []
    records: list[dict] = []
    tokens_in = tokens_out = 0

    for c in wanted:
        text = _chunk_text(chunks, turns_by_id, c)
        if len(text) > MAX_PROMPT_CHARS:
            text = text[:MAX_PROMPT_CHARS]
        prompt = (
            f"Transcript chunk {c['chunk_id']} (source {source}, file {filename}).\n"
            f"Existing topic labels (reuse verbatim when they fit): {json.dumps(existing)}\n"
            f"CHUNK:\n{text}"
        )
        out = r.complete_json(prompt, system=_SYSTEM)
        tokens_in += _usage(r, "in")
        tokens_out += _usage(r, "out")

        chunk_topics = out.get("topics") or []
        chunk_transitions = out.get("transitions") or []
        for t in chunk_topics:
            label = str(t.get("label", "")).strip()
            if not label:
                continue
            # label-stability: reuse the exact existing spelling when a label
            # matches case-insensitively
            for el in existing:
                if label.casefold() == el.casefold():
                    label = el
                    break
            tid = _topic_id_for(label, assigned)
            topics_out.append({
                "topic_id": tid,
                "label": label,
                "chunks": [c["chunk_id"]],
                "turn_ids": list(c["turn_ids"]),
                "supporting_quotes": _map_quotes(t.get("quotes") or [], c, turns_by_id),
                "intensity": float(t.get("intensity", 0.0)),
                "span_start": c["char_offset"],
                "span_end": c["char_offset"] + c["char_length"],
            })
            records.append({
                "annotation_id": _aid(f"tp-{c['chunk_id']}-{tid}"),
                "layer": "topic", "kind": "topic",
                "target": {"source": source, "filename": filename, "chunk_id": c["chunk_id"]},
                "revision": 1,
                "payload": {"topic_id": tid, "label": label, "intensity": float(t.get("intensity", 0.0))},
                "created_at": "2026-08-12T00:00:00Z",
            })
        for tr in chunk_transitions:
            f_label = str(tr.get("from_label", "")).strip()
            t_label = str(tr.get("to_label", "")).strip()
            f_tid = assigned.get(f_label.casefold())
            t_tid = assigned.get(t_label.casefold())
            if f_tid is None or t_tid is None:
                continue  # transitions referencing unseen labels are dropped
            transitions_out.append({
                "from_topic_id": f_tid, "to_topic_id": t_tid,
                "position": c["chunk_id"], "type": str(tr.get("type", "contiguous")),
                "signal_text": str(tr.get("signal_text", "")),
            })
            records.append({
                "annotation_id": _aid(f"tr-{c['chunk_id']}-{f_tid}-{t_tid}"),
                "layer": "transition", "kind": "transition",
                "target": {"source": source, "filename": filename, "chunk_id": c["chunk_id"]},
                "revision": 1,
                "payload": {"from_topic_id": f_tid, "to_topic_id": t_tid,
                            "type": str(tr.get("type", "contiguous")),
                            "signal_text": str(tr.get("signal_text", ""))},
                "created_at": "2026-08-12T00:00:00Z",
            })

    # merge topic entries that appeared in multiple chunks (same label → merge chunks/turns/spans)
    merged: dict[str, dict] = {}
    for t in topics_out:
        if t["topic_id"] not in merged:
            merged[t["topic_id"]] = t
        else:
            m = merged[t["topic_id"]]
            m["chunks"] = sorted(set(m["chunks"] + t["chunks"]))
            m["turn_ids"] = _dedupe_ordered(m["turn_ids"] + t["turn_ids"])
            m["supporting_quotes"] = (m["supporting_quotes"] + t["supporting_quotes"])[:10]
            m["span_start"] = min(m["span_start"], t["span_start"])
            m["span_end"] = max(m["span_end"], t["span_end"])
            m["intensity"] = max(m["intensity"], t["intensity"])
    topics = [merged[k] for k in sorted(merged)]

    response = {
        "pass_id": PASS_ID,
        "pass_version": PASS_VERSION,
        "topics": topics,
        "transitions": transitions_out,
        "records": records,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "records_sha256": sha256_json(records),
    }
    errs = validate_against_schema(response, bundle_schema_path(PASS_ID, "response.schema.json"))
    if errs:
        raise RuntimeError(f"topic-transition response failed schema: {errs}")

    as_mod.AnnotationStore(store).append(PASS_ID, PASS_VERSION, records)
    return response


def _map_quotes(quotes: list, chunk: dict, turns_by_id: dict[str, dict]) -> list[dict]:
    out = []
    for q in quotes:
        qtext = str(q).strip()
        if not qtext:
            continue
        for tid in chunk["turn_ids"]:
            if qtext in turns_by_id[tid].get("content", ""):
                out.append({"chunk_id": chunk["chunk_id"], "event_id": tid, "text": qtext})
                break
    return out


def _usage(router: Any, side: str) -> int:
    attr = "last_usage"
    last = getattr(router, attr, None)
    if isinstance(last, dict):
        return int(last.get(f"tokens_{side}", 0))
    return 0


def _dedupe_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _aid(slug: str) -> str:
    import hashlib
    import re
    s = re.sub(r"[^a-z0-9-]", "-", slug).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if len(s) < 8:
        s = s + "-" + hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    return s[:64]
