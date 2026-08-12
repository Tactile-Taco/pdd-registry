"""transcript-pipeline common: hashing, jsonl, fidelity, archive access, schemas.

Pure stdlib. Deterministic by construction (no time, no random, no network).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterable, NamedTuple, Optional

# --------------------------------------------------------------------------
# Hashing / jsonl
# --------------------------------------------------------------------------

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def read_jsonl(path: str) -> Iterable[dict[str, Any]]:
    """Yield parsed JSON objects from a JSONL file, skipping blank lines."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def append_jsonl(path: str, record: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Fidelity classes (per transcript source)
# --------------------------------------------------------------------------

# full = append-only archive; compaction does not lose history
# lossy = harness rewrites/compacts history (claude, codex)
FIDELITY: dict[str, str] = {
    "reasonix": "full",
    "omp": "full",
    "claude": "lossy",
    "codex": "lossy",
    "kimi": "full",
    "hermes": "full",
}


def fidelity_for(source: str) -> str:
    return FIDELITY.get(source, "lossy")


# --------------------------------------------------------------------------
# Archive access (read-only by construction)
# --------------------------------------------------------------------------

ARCHIVE_BASE = os.environ.get("TRANSCRIPT_ARCHIVE", "/home/tacticaltaco/transcript-archive")


def archive_source_dir(source: str) -> str:
    return os.path.join(ARCHIVE_BASE, source)


def list_transcripts(source: str, archive_base: str | None = None) -> list[str]:
    """Absolute paths of transcript files in one source dir, sorted."""
    d = archive_source_dir(source) if archive_base is None else os.path.join(archive_base, source)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, n) for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars/token (stdlib-only, deterministic)."""
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------
# Canonical turns
# --------------------------------------------------------------------------

class Turn(NamedTuple):
    event_id: str
    role: str
    content: str
    reasoning: str = ""
    model: str = ""
    compacted: bool = False


def turn_text(turn: Turn) -> str:
    """Canonical rendered text for one turn (byte-deterministic)."""
    head = f"[{turn.event_id}][{turn.role}]"
    body = []
    if turn.reasoning:
        body.append("> reasoning: " + turn.reasoning)
    body.append(turn.content)
    return head + "\n" + "\n".join(body) + "\n"


def render_text(turns: list[Turn]) -> str:
    return "".join(turn_text(t) for t in turns)


def _normalize_content(content: Any) -> str:
    """Normalize string-or-blocks content to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif isinstance(b.get("content"), str):
                    parts.append(b["content"])
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    if isinstance(content, dict) and isinstance(content.get("content"), str):
        return content["content"]
    return str(content)


# --------------------------------------------------------------------------
# Bundle schema helpers (for schema-conformance validation in candidates)
# --------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# implementations/_pipeline_common/python-stdlib/common.py -> repo root is 4 up
# (python-stdlib -> _pipeline_common -> implementations -> repo root)


def bundle_schema_path(bundle: str, schema_file: str) -> str:
    return os.path.join(REPO_ROOT, "pdd-bundles", bundle, "schemas", schema_file)


def validate_against_schema(obj: Any, schema_path: str) -> list[str]:
    """Return a list of jsonschema validation error strings (empty = valid)."""
    import jsonschema

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    try:
        jsonschema.validate(obj, schema)
        return []
    except jsonschema.ValidationError as e:
        return [e.message]


def session_key(source: str, filename: str) -> str:
    """Deterministic [a-z0-9-]+ key for graph node ids."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    stem = re.sub(r"-{2,}", "-", stem) or "session"
    return f"{source}-{stem}"
