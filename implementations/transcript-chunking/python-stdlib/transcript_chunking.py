"""transcript-chunking — attested candidate core (pure, self-contained).

Attestation surface: pure logic only (no filesystem, network, or time).
The backlog runner provides the I/O (archive reads, chunk-store writes);
this module attests the rendering + strict-partition chunk-map logic.

Imports are restricted to the validator's stdlib allowlist; sha256 and the
tiny statistics helpers are implemented inline so the core needs nothing else.
"""

import json
import re
from typing import Dict, List, Optional, Tuple

# Error envelope kinds (S-002: referenced in candidate source)
ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

PASS_ID = "transcript-chunking"
PASS_VERSION = "0.1.0-draft"

FIDELITY: Dict[str, str] = {
    "reasonix": "full", "omp": "full", "claude": "lossy", "codex": "lossy",
    "kimi": "full", "hermes": "full",
}

SOURCES = ("reasonix", "omp", "claude", "codex", "kimi", "hermes")


# --------------------------------------------------------------------------
# sha256 (pure python; deterministic; tests cross-check against hashlib)
# --------------------------------------------------------------------------

_K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]
_H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
       0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]


def _rotr(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def sha256_hex(data: bytes) -> str:
    """SHA-256 of bytes, hex string (pure python, big-endian words)."""
    ml = len(data) * 8
    data = data + b"\x80" + b"\x00" * ((56 - (len(data) + 1) % 64) % 64)
    data += ml.to_bytes(8, "big")
    h = list(_H0)
    for off in range(0, len(data), 64):
        w = [int.from_bytes(data[off + 4 * i:off + 4 * i + 4], "big") for i in range(16)]
        for i in range(16, 64):
            s0 = _rotr(w[i - 15], 7) ^ _rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
            s1 = _rotr(w[i - 2], 17) ^ _rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
            w.append((w[i - 16] + s0 + w[i - 7] + s1) & 0xFFFFFFFF)
        a, b, c, d, e, f, g, hh = h
        for i in range(64):
            S1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
            ch = (e & f) ^ (~e & g)
            t1 = (hh + S1 + ch + _K[i] + w[i]) & 0xFFFFFFFF
            S0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
            maj = (a & b) ^ (a & c) ^ (b & c)
            t2 = (S0 + maj) & 0xFFFFFFFF
            hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & 0xFFFFFFFF, c, b, a, (t1 + t2) & 0xFFFFFFFF
        h = [(x + y) & 0xFFFFFFFF for x, y in zip(h, [a, b, c, d, e, f, g, hh])]
    return "".join(f"{x:08x}" for x in h)


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def sha256_json(obj) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------
# canonical turns
# --------------------------------------------------------------------------

def _normalize_content(content) -> str:
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


def turn_text(turn: dict) -> str:
    head = f"[{turn['event_id']}][{turn['role']}]"
    body = []
    if turn.get("reasoning"):
        body.append("> reasoning: " + turn["reasoning"])
    body.append(turn.get("content", ""))
    return head + "\n" + "\n".join(body) + "\n"


def render_text(turns: List[dict]) -> str:
    return "".join(turn_text(t) for t in turns)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------
# source renderers (from parsed JSONL lines — pure)
# --------------------------------------------------------------------------

def render_turns(source: str, lines: List[str], harness: Optional[str] = None) -> List[dict]:
    if source not in SOURCES:
        raise ValueError("invalid_request: unknown source")
    turns: List[dict] = []
    for li, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # malformed line: skip (fidelity is best-effort per line)
        if not isinstance(ev, dict):
            continue  # non-object record: skip
        if source == "reasonix":
            for mi, m in enumerate(ev.get("messages") or []):
                turns.append({"event_id": f"e{li}-{mi}", "role": str(m.get("role", "unknown")),
                              "content": _normalize_content(m.get("content"))})
        elif source == "omp":
            if ev.get("type") not in ("user", "assistant", "tool"):
                continue
            content = ev.get("content")
            if content is None:
                content = (ev.get("message") or {}).get("content")
            turns.append({"event_id": str(ev.get("id") or f"e{li}"),
                          "role": ev["type"], "content": _normalize_content(content)})
        elif source == "claude":
            if ev.get("type") not in ("user", "assistant"):
                continue
            msg = ev.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            turns.append({"event_id": str(msg.get("id") or ev.get("uuid") or f"e{li}"),
                          "role": ev["type"], "content": _normalize_content(content)})
        elif source == "codex":
            if ev.get("type") not in ("user", "assistant", "system"):
                continue
            msg = ev.get("message") or {}
            content = msg.get("content") or ev.get("content")
            turns.append({"event_id": str(msg.get("id") or f"e{li}"),
                          "role": ev["type"], "content": _normalize_content(content)})
        elif source == "kimi":
            role = str(ev.get("role", ""))
            if role.startswith("_") or role not in ("user", "assistant", "tool"):
                continue
            turns.append({"event_id": f"e{li}", "role": role,
                          "content": _normalize_content(ev.get("content"))})
        elif source == "hermes":
            role = str(ev.get("role", ""))
            if role not in ("user", "assistant"):
                continue
            turns.append({"event_id": f"e{li}", "role": role,
                          "content": _normalize_content(ev.get("content")),
                          "reasoning": _normalize_content(ev.get("reasoning_content")),
                          "model": str(ev.get("model") or ""),
                          "compacted": bool(ev.get("compacted"))})
    return turns


# --------------------------------------------------------------------------
# chunk map — strict partition of turns
# --------------------------------------------------------------------------

def build_chunks(turns: List[dict], target_chars: int) -> Tuple[str, List[dict]]:
    render = render_text(turns)
    chunks: List[dict] = []
    offset = 0
    cur: List[dict] = []
    cur_len = 0
    target = max(10000, int(target_chars))

    def flush() -> None:
        nonlocal offset, cur, cur_len
        if not cur:
            return
        text = "".join(turn_text(t) for t in cur)
        chunks.append({
            "chunk_id": f"c{len(chunks)}",
            "turn_ids": [t["event_id"] for t in cur],
            "char_offset": offset,
            "char_length": len(text),
            "sha256": sha256_text(text),
            "est_tokens": estimate_tokens(text),
        })
        offset += len(text)
        cur = []
        cur_len = 0

    for t in turns:
        tl = len(turn_text(t))
        if cur and cur_len + tl > target:
            flush()
        cur.append(t)
        cur_len += tl
    flush()
    return render, chunks


def build_response(source: str, filename: str, turns: List[dict],
                   render: str, chunks: List[dict]) -> dict:
    return {
        "render_id": f"{source}-{filename}-{sha256_text(render)[:12]}",
        "source": source,
        "filename": filename,
        "fidelity_class": FIDELITY.get(source, "lossy"),
        "chunks": chunks,
        "render_sha256": sha256_text(render),
        "stats": {"turn_count": len(turns), "chunk_count": len(chunks),
                  "total_chars": len(render)},
    }
