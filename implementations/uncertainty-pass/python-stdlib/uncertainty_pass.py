"""uncertainty-pass implementation (draft bundle candidate).

LLM-free marker-density annotation over rendered chunks: per-chunk density
stats (per-1k, diversity, positional median, variance), reasoning-vs-dialogue
source segregation, and contention events on user turns. Deterministic.
"""

from __future__ import annotations

import json
import os
import statistics
from typing import Any, Optional

from common import (
    bundle_schema_path,
    read_jsonl,
    sha256_json,
    validate_against_schema,
)

PASS_ID = "uncertainty-pass"
PASS_VERSION = "0.1.0-draft"
LEXICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lexicons")


class Lexicon:
    def __init__(self, name: str, version: str, markers: list[str]) -> None:
        self.name = name
        self.version = version
        # longest-first so multiword markers win over their prefixes deterministically
        self.markers = sorted(markers, key=lambda m: (-len(m), m))

    @staticmethod
    def load(path: str) -> "Lexicon":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return Lexicon(d["name"], d["version"], d["markers"])

    @staticmethod
    def load_all(lexicon_dir: str = LEXICON_DIR) -> dict[str, "Lexicon"]:
        out = {}
        for fn in sorted(os.listdir(lexicon_dir)):
            if fn.endswith(".json"):
                lex = Lexicon.load(os.path.join(lexicon_dir, fn))
                out[lex.name] = lex
        return out


def lexicon_version(lexicons: dict[str, Lexicon]) -> str:
    return "+".join(f"{n}@{l.version}" for n, l in sorted(lexicons.items()))


def scan(text: str, markers: list[str]) -> list[tuple[str, int]]:
    """Return [(marker, char_index)] in left-to-right scan order.

    Deterministic: at each position the longest matching marker wins and the
    scan advances past it (no overlapping matches).
    """
    out: list[tuple[str, int]] = []
    low = text.casefold()
    i = 0
    n = len(low)
    while i < n:
        matched = None
        for m in markers:  # longest-first
            ml = len(m)
            if i + ml <= n and low[i:i + ml] == m:
                matched = m
                break
        if matched is not None:
            out.append((matched, i))
            i += len(matched)
        else:
            i += 1
    return out


def run(source: str, filename: str, render_id: str, lexicon_version_: str,
        emit_layers: Optional[list[str]] = None,
        chunk_store: Optional[str] = None,
        store_dir: Optional[str] = None) -> dict:
    """Annotate one transcript's rendered chunks. Writes records into the
    annotation store (append) and returns the pass response."""
    import annotation_store as as_mod

    store = store_dir or os.environ.get("ANNOTATION_STORE", "./annotation-store")
    cs = chunk_store or os.path.join(store, "chunk-store")
    turns_path = os.path.join(cs, source, filename + ".render.jsonl")
    cm_path = os.path.join(cs, source, filename + ".chunkmap.json")
    if not os.path.exists(turns_path) or not os.path.exists(cm_path):
        raise FileNotFoundError(f"render/chunk map not materialized for {source}/{filename}")

    with open(cm_path, "r", encoding="utf-8") as f:
        chunkmap = json.load(f)
    chunks = chunkmap["chunks"]
    turns = list(read_jsonl(turns_path))
    turn_by_id = {t["event_id"]: t for t in turns}
    if chunkmap.get("render_id") != render_id:
        raise ValueError(f"render_id mismatch: {chunkmap.get('render_id')} != {render_id}")

    lexicons = Lexicon.load_all()
    if lexicon_version_ and lexicon_version_ != lexicon_version(lexicons):
        raise ValueError(f"lexicon_version mismatch: requested {lexicon_version_}, have {lexicon_version(lexicons)}")
    unc = lexicons["uncertainty"]
    plan = lexicons["planning"]
    cont = lexicons["contention"]
    all_markers = unc.markers + plan.markers + cont.markers

    # chunk text with a parallel source mask: 'r' = reasoning, 'd' = dialogue
    def chunk_text_and_mask(chunk_turns: list[dict]) -> tuple[str, str]:
        text: list[str] = []
        mask: list[str] = []
        for t in chunk_turns:
            head = f"[{t['event_id']}][{t['role']}]\n"
            text.append(head)
            mask.append("d" * len(head))
            if t.get("reasoning"):
                line = "> reasoning: " + t["reasoning"] + "\n"
                text.append(line)
                mask.append("r" * len(line))
            content = t.get("content", "") + "\n"
            text.append(content)
            mask.append("d" * len(content))
        return "".join(text), "".join(mask)

    density_out = []
    records: list[dict] = []
    for c in chunks:
        chunk_turns = [turn_by_id[tid] for tid in c["turn_ids"]]
        ctext, cmask = chunk_text_and_mask(chunk_turns)
        matches = scan(ctext, all_markers)
        counts: dict[str, int] = {}
        positions: list[tuple[int, str]] = []  # (char_index, source)
        for m, pos in matches:
            counts[m] = counts.get(m, 0) + 1
            src_class = cmask[pos] if pos < len(cmask) else "d"
            positions.append((pos, src_class))
        n = len(matches)
        char_count = len(ctext)
        src = "reasoning" if positions and all(s == "r" for _p, s in positions) else (
            "dialogue" if positions and all(s == "d" for _p, s in positions) else "both")
        pcts = [pos / char_count * 100.0 for pos, _s in positions] if char_count else []
        density = {
            "chunk_id": c["chunk_id"],
            "char_count": char_count,
            "marker_count": n,
            "density_per_1k": round(n * 1000.0 / char_count, 4) if char_count else 0.0,
            "markers": counts,
            "diversity": len(counts),
            "positional_median_pct": round(statistics.median(pcts), 4) if pcts else 0.0,
            "variance": round(statistics.pvariance(pcts), 4) if len(pcts) >= 2 else 0.0,
            "source": src,
        }
        density_out.append(density)
        records.append({
            "annotation_id": _aid(f"u-{c['chunk_id']}"),
            "layer": "uncertainty",
            "kind": "marker-span",
            "target": {"source": source, "filename": filename, "chunk_id": c["chunk_id"]},
            "revision": 1,
            "payload": {"marker_counts": counts, "match_count": n, "density_per_1k": density["density_per_1k"]},
            "created_at": "2026-08-12T00:00:00Z",
        })

    # contention events: user turns whose dialogue text matches contention markers
    for t in turns:
        if t.get("role") not in ("user",):
            continue
        hits = [m for m, _p in scan(t.get("content", ""), cont.markers)]
        if hits:
            records.append({
                "annotation_id": _aid(f"ct-{t['event_id']}"),
                "layer": "contention",
                "kind": "contention-event",
                "target": {"source": source, "filename": filename, "event_id": t["event_id"]},
                "revision": 1,
                "payload": {"markers": hits},
                "created_at": "2026-08-12T00:00:00Z",
            })

    response = {
        "pass_id": PASS_ID,
        "pass_version": PASS_VERSION,
        "lexicon_version": lexicon_version(lexicons),
        "chunks_processed": len(chunks),
        "records": records,
        "density": density_out,
        "records_sha256": sha256_json(records),
    }
    errs = validate_against_schema(response, bundle_schema_path(PASS_ID, "response.schema.json"))
    if errs:
        raise RuntimeError(f"uncertainty response failed schema: {errs}")

    store_obj = as_mod.AnnotationStore(store)
    store_obj.append(PASS_ID, PASS_VERSION, records)
    return response


def _aid(slug: str) -> str:
    import hashlib
    import re
    s = re.sub(r"[^a-z0-9-]", "-", slug).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if len(s) < 8:  # schema requires [a-z0-9-]{8,64}
        s = s + "-" + hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    return s[:64]
