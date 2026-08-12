"""annotation-store — attested candidate core (pure, self-contained).

Pure append/query/render logic over in-memory record lists. The runner
provides file persistence (the deployment surface); this module attests the
envelope validation, append-only supersede semantics, and marker stitching.
"""

import json
import re
from typing import Dict, List, Optional

from _hash import sha256_json

ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

MAX_BATCH = 10000
_ID_RE = re.compile(r"^[a-z0-9-]{8,64}$")
_CHUNK_RE = re.compile(r"^c[0-9]+$")
_SOURCE_RE = re.compile(r"^(reasonix|omp|claude|codex|kimi|hermes)$")
_LAYER_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _target_key(target: dict) -> tuple:
    return (target.get("source"), target.get("filename"),
            target.get("event_id"), target.get("chunk_id"))


def validate_record(record: dict, chunk_ids: Optional[set] = None) -> Optional[str]:
    """Return an error message or None. Structural envelope validation."""
    if not isinstance(record, dict):
        return "record must be an object"
    missing = [k for k in ("annotation_id", "layer", "kind", "target",
                           "revision", "payload", "created_at") if k not in record]
    if missing:
        return "invalid_request: missing " + ",".join(missing)
    if not _ID_RE.fullmatch(record["annotation_id"]):
        return "invalid_request: bad annotation_id"
    if not _LAYER_RE.fullmatch(record["layer"]) or not _KIND_RE.fullmatch(record["kind"]):
        return "invalid_request: bad layer/kind"
    if not isinstance(record["revision"], int) or record["revision"] < 1:
        return "invalid_request: bad revision"
    target = record["target"]
    if not isinstance(target, dict):
        return "invalid_request: bad target"
    if not _SOURCE_RE.fullmatch(str(target.get("source", ""))):
        return "invalid_request: bad source"
    if not isinstance(target.get("filename"), str) or not target["filename"]:
        return "invalid_request: bad filename"
    if not target.get("event_id") and not target.get("chunk_id"):
        return "invalid_request: target needs event_id or chunk_id"
    if target.get("chunk_id") is not None:
        if not _CHUNK_RE.fullmatch(str(target["chunk_id"])):
            return "invalid_request: bad chunk_id"
        if chunk_ids is not None and target["chunk_id"] not in chunk_ids:
            return "conflict: chunk_id does not resolve"
    return None


def supersede_key(record: dict) -> tuple:
    return (record["pass_id"], record["layer"], record["kind"], _target_key(record["target"]))


class AnnotationCore:
    """Pure store semantics over an in-memory list of stored records."""

    def __init__(self) -> None:
        self.records: List[dict] = []

    def append(self, pass_id: str, pass_version: str, records: List[dict],
               chunk_ids: Optional[set] = None) -> dict:
        if not 1 <= len(records) <= MAX_BATCH:
            raise ValueError("invalid_request: batch size outside 1..10000")
        stored = []
        seen: set = set()
        existing_ids = {r["annotation_id"]: _target_key(r["target"]) for r in self.records}
        for r in records:
            err = validate_record(r, chunk_ids)
            if err:
                raise ValueError(err)
            if r["annotation_id"] in seen:
                raise ValueError("invalid_request: duplicate annotation_id in batch")
            seen.add(r["annotation_id"])
            if r["annotation_id"] in existing_ids and \
                    existing_ids[r["annotation_id"]] != _target_key(r["target"]):
                raise ValueError("invalid_request: annotation_id reused for a different target")
            stored.append({"pass_id": pass_id, "pass_version": pass_version, **r})

        superseded = 0
        keyed: Dict[tuple, int] = {}
        for rec in self.records:
            k = supersede_key(rec)
            keyed[k] = max(keyed.get(k, 0), rec["revision"])
        for r in stored:
            k = (r["pass_id"], r["layer"], r["kind"], _target_key(r["target"]))
            if k in keyed and keyed[k] <= r["revision"]:
                superseded += 1
        # append-only: records are never mutated or deleted
        self.records.extend(stored)
        return {"accepted_count": len(stored), "superseded_count": superseded,
                "store_sha256": sha256_json(self.records)}

    def query(self, source: str, filename: str, chunk_id: Optional[str] = None,
              pass_id: Optional[str] = None, layer: Optional[str] = None,
              kind: Optional[str] = None, min_revision: Optional[int] = None) -> dict:
        matched = []
        for rec in self.records:
            if rec["target"].get("source") != source or rec["target"].get("filename") != filename:
                continue
            if chunk_id is not None and rec["target"].get("chunk_id") != chunk_id:
                continue
            if pass_id is not None and rec["pass_id"] != pass_id:
                continue
            if layer is not None and rec["layer"] != layer:
                continue
            if kind is not None and rec["kind"] != kind:
                continue
            if min_revision is not None and rec["revision"] < min_revision:
                continue
            matched.append(rec)
        total = len(matched)
        best: Dict[tuple, dict] = {}
        for rec in matched:
            k = supersede_key(rec)
            if k not in best or rec["revision"] > best[k]["revision"]:
                best[k] = rec
        records = sorted(best.values(), key=lambda r: (r["target"].get("event_id") or "",
                                                       r["target"].get("chunk_id") or "",
                                                       r["revision"]))
        return {"records": records, "total_matches": total}

    def render(self, turns: List[dict], chunk_map: Dict, layers: List[str],
               marker_style: str = "bracketed") -> dict:
        """Stitch markers into the canonical render (chunk_id-targeted markers
        land on the chunk's first turn)."""
        first_of_chunk: Dict[str, str] = {}
        for c in chunk_map.get("chunks", []):
            if c.get("turn_ids"):
                first_of_chunk[c["chunk_id"]] = c["turn_ids"][0]
        by_event: Dict[str, List[dict]] = {}
        for rec in self.records:
            if rec["layer"] not in layers:
                continue
            event = rec["target"].get("event_id")
            if event is None and rec["target"].get("chunk_id"):
                event = first_of_chunk.get(rec["target"]["chunk_id"])
            if event:
                by_event.setdefault(event, []).append(rec)
        precedence = {l: i for i, l in enumerate(layers)}
        applied = 0
        out: List[str] = []
        for t in turns:
            lines = [f"[{t['event_id']}][{t['role']}]"]
            markers = sorted(by_event.get(t["event_id"], []),
                             key=lambda r: (precedence.get(r["layer"], 99), r["kind"], r["annotation_id"]))
            for m in markers:
                applied += 1
                if marker_style == "explicit" and isinstance(m["payload"].get("marker_text"), str):
                    lines.append(m["payload"]["marker_text"])
                else:
                    lines.append(f"[{m['layer']}:{m['kind']}]")
            if t.get("reasoning"):
                lines.append("> reasoning: " + t["reasoning"])
            lines.append(t.get("content", ""))
            out.append("\n".join(lines) + "\n")
        stitched = "".join(out)
        from _hash import sha256_text
        return {"render_sha256": sha256_text(stitched),
                "applied_record_count": applied, "stitched_text": stitched}
