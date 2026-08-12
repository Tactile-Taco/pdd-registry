"""uncertainty-pass — attested candidate core (pure, self-contained).

LLM-free marker-density annotation: pinned lexicons (module constants),
deterministic left-to-right scan, per-chunk density (per-1k, diversity,
positional median, variance), reasoning-vs-dialogue source segregation, and
contention events on user turns. No I/O, no network, no time.
"""

import re
from typing import Dict, List, Optional

from _hash import sha256_json

ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

PASS_ID = "uncertainty-pass"
PASS_VERSION = "0.1.0-draft"
LEXICON_VERSION = "uncertainty@1.0.0+planning@1.0.0+contention@1.0.0"

# Pinned lexicons (v1.0.0). Files keep stable order; longest-first is applied
# by the Lexicon constructor so multiword markers win over their prefixes.
UNCERTAINTY_MARKERS = [
    "actually", "apparently", "as far as i know", "but wait", "could be",
    "hmm", "i believe", "i don't know", "i dont know", "i guess", "i might",
    "i recall", "i remember", "i suppose", "i think", "i'd say",
    "i'm fairly sure", "i'm not sure", "i'm pretty sure", "if i'm not mistaken",
    "let me reconsider", "let me think", "maybe", "might be", "not entirely",
    "not sure", "on second thought", "perhaps", "possibly", "probably",
    "roughly", "seems like", "somewhat", "tentatively", "to my knowledge",
    "uncertain", "unsure", "wait,",
]
PLANNING_MARKERS = [
    "approach", "before we", "begin by", "first,", "firstly", "goal",
    "i will", "i'll first", "i'll start", "i'm going to", "let me", "let's",
    "let's start", "next,", "objective", "outline", "plan", "start by",
    "step 1", "step 2", "strategy", "then,", "we need to",
]
CONTENTION_MARKERS = [
    "actually, that", "but", "disagree", "error:", "failed", "however",
    "incorrect", "it failed", "misunderstanding", "no,", "not what i",
    "not what i asked", "on the contrary", "revert", "rollback", "stop",
    "that didn't", "that didn't work", "that's not", "that's wrong", "wrong",
    "you didn't", "you're wrong",
]

ALL_MARKERS = sorted(
    set(UNCERTAINTY_MARKERS) | set(PLANNING_MARKERS) | set(CONTENTION_MARKERS),
    key=lambda m: (-len(m), m))


def scan(text: str, markers: Optional[List[str]] = None) -> List[tuple]:
    """[(marker, char_index)] in left-to-right order, longest-match-first,
    no overlapping matches."""
    ms = markers if markers is not None else ALL_MARKERS
    ms = sorted(ms, key=lambda m: (-len(m), m))
    out: List[tuple] = []
    low = text.casefold()
    i, n = 0, len(low)
    while i < n:
        matched = None
        for m in ms:
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


def _chunk_text_and_mask(chunk_turns: List[dict]) -> tuple:
    text: List[str] = []
    mask: List[str] = []
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


def run(source: str, filename: str, chunk_map: dict, turns: List[dict]) -> dict:
    """Pure annotation over in-memory chunk map + turns."""
    if source not in ("reasonix", "omp", "claude", "codex", "kimi", "hermes"):
        raise ValueError("invalid_request: unknown source")
    turn_by_id = {t["event_id"]: t for t in turns}
    chunks = chunk_map.get("chunks", [])

    density_out: List[dict] = []
    records: List[dict] = []
    for c in chunks:
        chunk_turns = [turn_by_id[tid] for tid in c.get("turn_ids", []) if tid in turn_by_id]
        ctext, cmask = _chunk_text_and_mask(chunk_turns)
        matches = scan(ctext)
        counts: Dict[str, int] = {}
        positions: List[tuple] = []
        for m, pos in matches:
            counts[m] = counts.get(m, 0) + 1
            positions.append((pos, cmask[pos] if pos < len(cmask) else "d"))
        n = len(matches)
        char_count = len(ctext)
        src = "both"
        if positions:
            src = ("reasoning" if all(s == "r" for _p, s in positions)
                   else "dialogue" if all(s == "d" for _p, s in positions) else "both")
        pcts = [pos / char_count * 100.0 for pos, _s in positions] if char_count else []
        density_out.append({
            "chunk_id": c["chunk_id"],
            "char_count": char_count,
            "marker_count": n,
            "density_per_1k": round(n * 1000.0 / char_count, 4) if char_count else 0.0,
            "markers": counts,
            "diversity": len(counts),
            "positional_median_pct": round(_median(pcts), 4) if pcts else 0.0,
            "variance": round(_pvariance(pcts), 4) if len(pcts) >= 2 else 0.0,
            "source": src,
        })
        records.append({
            "annotation_id": _aid(f"u-{c['chunk_id']}"),
            "layer": "uncertainty", "kind": "marker-span",
            "target": {"source": source, "filename": filename, "chunk_id": c["chunk_id"]},
            "revision": 1,
            "payload": {"marker_counts": counts, "match_count": n,
                        "density_per_1k": density_out[-1]["density_per_1k"]},
            "created_at": "2026-08-12T00:00:00Z",
        })

    for t in turns:
        if t.get("role") != "user":
            continue
        hits = [m for m, _p in scan(t.get("content", ""), CONTENTION_MARKERS)]
        if hits:
            records.append({
                "annotation_id": _aid(f"ct-{t['event_id']}"),
                "layer": "contention", "kind": "contention-event",
                "target": {"source": source, "filename": filename,
                           "event_id": t["event_id"]},
                "revision": 1,
                "payload": {"markers": hits},
                "created_at": "2026-08-12T00:00:00Z",
            })

    return {
        "pass_id": PASS_ID,
        "pass_version": PASS_VERSION,
        "lexicon_version": LEXICON_VERSION,
        "chunks_processed": len(chunks),
        "records": records,
        "density": density_out,
        "records_sha256": sha256_json(records),
    }


def _median(xs: List[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _pvariance(xs: List[float]) -> float:
    mean = sum(xs) / len(xs)
    return sum((x - mean) ** 2 for x in xs) / len(xs)


def _aid(slug: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9-]", "-", slug).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if len(s) < 8:
        s = s + "-" + _re.sub(r"[^a-f0-9]", "", sha256_json({"s": slug}))[:8]
    return s[:64]
