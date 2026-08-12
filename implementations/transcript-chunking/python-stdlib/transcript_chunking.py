"""transcript-chunking implementation (draft bundle candidate).

Renders archived transcripts from 6 sources into canonical turns and builds a
strict-partition chunk map. Deterministic, LLM-free, archive read-only.

Canonical render format (byte-deterministic):
    [<event_id>][<role>]\n
    > reasoning: <reasoning>\n        (only when the turn carries reasoning)
    <content>\n
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from common import (
    Turn,
    estimate_tokens,
    fidelity_for,
    render_text,
    sha256_file,
    sha256_json,
    sha256_text,
    turn_text,
    validate_against_schema,
    bundle_schema_path,
    _normalize_content,
)

PASS_ID = "transcript-chunking"
PASS_VERSION = "0.1.0-draft"

# chunk store env override (where renders + chunk maps are materialized)
CHUNK_STORE = os.environ.get("TRANSCRIPT_CHUNK_STORE", os.path.join(os.environ.get("ANNOTATION_STORE", "./annotation-store"), "chunk-store"))


# --------------------------------------------------------------------------
# Source renderers — each yields canonical Turns in transcript order.
# --------------------------------------------------------------------------

def _render_reasonix(path: str) -> list[Turn]:
    turns: list[Turn] = []
    with open(path, "r", encoding="utf-8") as f:
        for li, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            msgs = ev.get("messages") or []
            for mi, m in enumerate(msgs):
                role = str(m.get("role", "unknown"))
                content = _normalize_content(m.get("content"))
                turns.append(Turn(event_id=f"e{li}-{mi}", role=role, content=content))
    return turns


def _render_omp(path: str) -> list[Turn]:
    turns: list[Turn] = []
    with open(path, "r", encoding="utf-8") as f:
        for li, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            etype = ev.get("type", "")
            if etype not in ("user", "assistant", "tool"):
                continue
            content = ev.get("content")
            if content is None:
                msg = ev.get("message") or {}
                content = msg.get("content")
            role = etype
            turns.append(Turn(event_id=str(ev.get("id") or f"e{li}"), role=role,
                              content=_normalize_content(content)))
    return turns


def _render_claude(path: str) -> list[Turn]:
    turns: list[Turn] = []
    with open(path, "r", encoding="utf-8") as f:
        for li, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            etype = ev.get("type", "")
            if etype in ("user", "assistant"):
                msg = ev.get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                turns.append(Turn(
                    event_id=str(msg.get("id") or ev.get("uuid") or f"e{li}"),
                    role=etype, content=_normalize_content(content)))
            # queue-operation and other bookkeeping lines carry no turns
    return turns


def _render_codex(path: str) -> list[Turn]:
    turns: list[Turn] = []
    with open(path, "r", encoding="utf-8") as f:
        for li, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            etype = ev.get("type", "")
            if etype not in ("user", "assistant", "system"):
                continue
            msg = ev.get("message") or {}
            content = msg.get("content") or ev.get("content")
            turns.append(Turn(event_id=str(msg.get("id") or f"e{li}"), role=etype,
                              content=_normalize_content(content)))
    return turns


def _render_kimi(path: str, harness: Optional[str] = None) -> list[Turn]:
    turns: list[Turn] = []
    with open(path, "r", encoding="utf-8") as f:
        for li, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            role = str(ev.get("role", ""))
            if role.startswith("_") or role not in ("user", "assistant", "tool"):
                continue
            content = _normalize_content(ev.get("content"))
            turns.append(Turn(event_id=f"e{li}", role=role, content=content))
    return turns


def _render_hermes(path: str) -> list[Turn]:
    turns: list[Turn] = []
    with open(path, "r", encoding="utf-8") as f:
        for li, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            role = str(ev.get("role", ""))
            if role not in ("user", "assistant"):
                continue
            turns.append(Turn(
                event_id=f"e{li}", role=role,
                content=_normalize_content(ev.get("content")),
                reasoning=_normalize_content(ev.get("reasoning_content")),
                model=str(ev.get("model") or ""),
                compacted=bool(ev.get("compacted")),
            ))
    return turns


RENDERERS = {
    "reasonix": _render_reasonix,
    "omp": _render_omp,
    "claude": _render_claude,
    "codex": _render_codex,
    "kimi": _render_kimi,
    "hermes": _render_hermes,
}


def render_turns(source: str, path: str, harness: Optional[str] = None) -> list[Turn]:
    renderer = RENDERERS[source]
    return renderer(path, harness) if source == "kimi" else renderer(path)


# --------------------------------------------------------------------------
# Chunk map — strict partition of turns.
# --------------------------------------------------------------------------

def build_chunks(turns: list[Turn], target_chars: int) -> tuple[str, list[dict]]:
    """Return (render_text, chunks). Chunks never split a turn; char ranges are
    contiguous and jointly cover the full render (strict partition)."""
    render = render_text(turns)
    chunks: list[dict] = []
    offset = 0
    cur_turns: list[str] = []
    cur_len = 0
    target = max(10000, target_chars)

    def flush() -> None:
        nonlocal offset, cur_turns, cur_len
        if not cur_turns:
            return
        text = "".join(turn_text(t) for t in cur_turns)
        chunks.append({
            "chunk_id": f"c{len(chunks)}",
            "turn_ids": [t.event_id for t in cur_turns],
            "char_offset": offset,
            "char_length": len(text),
            "sha256": sha256_text(text),
            "est_tokens": estimate_tokens(text),
        })
        offset += len(text)
        cur_turns = []
        cur_len = 0

    for t in turns:
        tl = len(turn_text(t))
        if cur_turns and cur_len + tl > target:
            flush()
        cur_turns.append(t)
        cur_len += tl
    flush()
    return render, chunks


# --------------------------------------------------------------------------
# Pass entry point
# --------------------------------------------------------------------------

def run(source: str, filename: str, archive_base: str, target_chars: int = 80000,
        harness: Optional[str] = None, chunk_store: Optional[str] = None) -> dict:
    store = chunk_store or CHUNK_STORE
    path = os.path.join(archive_base, source, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such transcript: {path}")
    turns = render_turns(source, path, harness)
    render, chunks = build_chunks(turns, target_chars)
    render_id = f"{source}-{filename}-{sha256_text(render)[:12]}"

    # materialize render + chunk map into the chunk store (never the archive)
    out_dir = os.path.join(store, source)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, filename + ".render.jsonl"), "w", encoding="utf-8") as f:
        for t in turns:
            rec = {"event_id": t.event_id, "role": t.role, "content": t.content,
                   "reasoning": t.reasoning, "model": t.model, "compacted": t.compacted}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    with open(os.path.join(out_dir, filename + ".chunkmap.json"), "w", encoding="utf-8") as f:
        json.dump({"render_id": render_id, "chunks": chunks,
                   "render_sha256": sha256_text(render)}, f, ensure_ascii=False, sort_keys=True)

    response = {
        "render_id": render_id,
        "source": source,
        "filename": filename,
        "fidelity_class": fidelity_for(source),
        "chunks": chunks,
        "render_sha256": sha256_text(render),
        "stats": {
            "turn_count": len(turns),
            "chunk_count": len(chunks),
            "total_chars": len(render),
        },
    }
    errors = validate_against_schema(response, bundle_schema_path(PASS_ID, "response.schema.json"))
    if errors:
        raise RuntimeError(f"chunking response failed schema: {errors}")
    return response


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="transcript-chunking")
    ap.add_argument("--source", required=True, choices=sorted(RENDERERS))
    ap.add_argument("--filename", required=True)
    ap.add_argument("--archive-base", default=os.environ.get("TRANSCRIPT_ARCHIVE", "/home/tacticaltaco/transcript-archive"))
    ap.add_argument("--target-chars", type=int, default=80000)
    ap.add_argument("--harness", default=None)
    args = ap.parse_args(argv)
    resp = run(args.source, args.filename, args.archive_base, args.target_chars, args.harness)
    print(json.dumps(resp, ensure_ascii=False, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
