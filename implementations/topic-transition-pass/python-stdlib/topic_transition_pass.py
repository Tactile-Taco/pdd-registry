"""topic-transition-pass — attested candidate core (pure, self-contained).

Per-chunk LLM topic+transition annotation. The router is duck-typed (must
expose complete_json(prompt, system=None)); no import of any router module,
so the attested core has zero network surface. Label consistency via
existing_labels; deterministic t0..tn topic ids in first-appearance order.
"""

import json
import re
from typing import Dict, List, Optional

from _hash import sha256_json

ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

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


def _chunk_text(chunks: Dict[str, dict], turns_by_id: Dict[str, dict], cid: str) -> str:
    parts = []
    for tid in chunks[cid].get("turn_ids", []):
        t = turns_by_id.get(tid)
        if t:
            parts.append(f"[{t['role']}] {t.get('content', '')}")
    return "\n".join(parts)


def run(source: str, filename: str, chunk_map: dict, turns: List[dict],
        existing_labels: Optional[List[str]] = None, router=None,
        chunk_ids: Optional[List[str]] = None) -> dict:
    if source not in ("reasonix", "omp", "claude", "codex", "kimi", "hermes"):
        raise ValueError("invalid_request: unknown source")
    chunks = {c["chunk_id"]: c for c in chunk_map.get("chunks", [])}
    turns_by_id = {t["event_id"]: t for t in turns}
    want = chunk_ids if chunk_ids is not None else sorted(chunks)
    missing = [c for c in want if c not in chunks]
    if missing:
        raise ValueError("invalid_request: chunk_ids not in chunk map: " + ",".join(missing))
    existing = [l for l in (existing_labels or [])]

    assigned: Dict[str, str] = {}
    topics_out: List[dict] = []
    transitions_out: List[dict] = []
    records: List[dict] = []
    tokens_in = tokens_out = 0

    for cid in want:
        c = chunks[cid]
        text = _chunk_text(chunks, turns_by_id, cid)
        if len(text) > MAX_PROMPT_CHARS:
            text = text[:MAX_PROMPT_CHARS]
        prompt = (f"Transcript chunk {cid} (source {source}, file {filename}).\n"
                  f"Existing topic labels (reuse verbatim when they fit): "
                  f"{json.dumps(existing)}\nCHUNK:\n{text}")
        out = router.complete_json(prompt, system=_SYSTEM)
        tokens_in += int(getattr(router, "last_usage", {}).get("tokens_in", 0))
        tokens_out += int(getattr(router, "last_usage", {}).get("tokens_out", 0))

        for t in (out.get("topics") or []):
            label = str(t.get("label", "")).strip()
            if not label:
                continue
            for el in existing:
                if label.casefold() == el.casefold():
                    label = el
                    break
            tid = _topic_id_for(label, assigned)
            topics_out.append({
                "topic_id": tid, "label": label, "chunks": [cid],
                "turn_ids": list(c.get("turn_ids", [])),
                "supporting_quotes": _map_quotes(t.get("quotes") or [], cid, chunks, turns_by_id),
                "intensity": float(t.get("intensity", 0.0)),
                "span_start": c.get("char_offset", 0),
                "span_end": c.get("char_offset", 0) + c.get("char_length", 0),
            })
            records.append({
                "annotation_id": _aid(f"tp-{cid}-{tid}"),
                "layer": "topic", "kind": "topic",
                "target": {"source": source, "filename": filename, "chunk_id": cid},
                "revision": 1,
                "payload": {"topic_id": tid, "label": label,
                            "intensity": float(t.get("intensity", 0.0))},
                "created_at": "2026-08-12T00:00:00Z",
            })
        for tr in (out.get("transitions") or []):
            f_tid = assigned.get(str(tr.get("from_label", "")).strip().casefold())
            t_tid = assigned.get(str(tr.get("to_label", "")).strip().casefold())
            if f_tid is None or t_tid is None:
                continue
            transitions_out.append({
                "from_topic_id": f_tid, "to_topic_id": t_tid, "position": cid,
                "type": str(tr.get("type", "contiguous")),
                "signal_text": str(tr.get("signal_text", "")),
            })
            records.append({
                "annotation_id": _aid(f"tr-{cid}-{f_tid}-{t_tid}"),
                "layer": "transition", "kind": "transition",
                "target": {"source": source, "filename": filename, "chunk_id": cid},
                "revision": 1,
                "payload": {"from_topic_id": f_tid, "to_topic_id": t_tid,
                            "type": str(tr.get("type", "contiguous")),
                            "signal_text": str(tr.get("signal_text", ""))},
                "created_at": "2026-08-12T00:00:00Z",
            })

    merged: Dict[str, dict] = {}
    for t in topics_out:
        if t["topic_id"] not in merged:
            merged[t["topic_id"]] = t
        else:
            m = merged[t["topic_id"]]
            m["chunks"] = sorted(set(m["chunks"] + t["chunks"]))
            m["turn_ids"] = _dedupe(m["turn_ids"] + t["turn_ids"])
            m["supporting_quotes"] = (m["supporting_quotes"] + t["supporting_quotes"])[:10]
            m["span_start"] = min(m["span_start"], t["span_start"])
            m["span_end"] = max(m["span_end"], t["span_end"])
            m["intensity"] = max(m["intensity"], t["intensity"])
    topics = [merged[k] for k in sorted(merged)]

    return {
        "pass_id": PASS_ID, "pass_version": PASS_VERSION,
        "topics": topics, "transitions": transitions_out, "records": records,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "records_sha256": sha256_json(records),
    }


def _topic_id_for(label: str, assigned: Dict[str, str]) -> str:
    key = label.casefold()
    if key in assigned:
        return assigned[key]
    tid = f"t{len(assigned)}"
    assigned[key] = tid
    return tid


def _map_quotes(quotes: list, cid: str, chunks: Dict[str, dict],
                turns_by_id: Dict[str, dict]) -> List[dict]:
    out = []
    for q in quotes:
        qtext = str(q).strip()
        if not qtext:
            continue
        for tid in chunks[cid].get("turn_ids", []):
            if qtext in turns_by_id.get(tid, {}).get("content", ""):
                out.append({"chunk_id": cid, "event_id": tid, "text": qtext})
                break
    return out


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _aid(slug: str) -> str:
    s = re.sub(r"[^a-z0-9-]", "-", slug).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if len(s) < 8:
        s = s + "-" + re.sub(r"[^a-f0-9]", "", sha256_json({"s": slug}))[:8]
    return s[:64]
