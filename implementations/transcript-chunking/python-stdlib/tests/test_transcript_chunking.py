"""Candidate tests for transcript-chunking (pure core; in-memory fixtures)."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transcript_chunking import (  # noqa: E402
    build_chunks, build_response, render_text, render_turns, sha256_hex,
    sha256_text, turn_text,
)


def test_sha256_matches_hashlib():
    for payload in (b"", b"abc", b"x" * 1000, "héllo wörld ✓".encode("utf-8")):
        assert sha256_hex(payload) == hashlib.sha256(payload).hexdigest()


def _lines(source, entries):
    if source == "reasonix":
        out = []
        for i, msgs in enumerate(entries):
            out.append(json.dumps({"schema_version": 1,
                                   "type": "append" if i else "replace",
                                   "revision": i + 1, "base_revision": i,
                                   "messages": msgs}))
        return out
    return [json.dumps(e) for e in entries]


def _turns(source="hermes"):
    return render_turns(source, _lines(source, [
        {"role": "user", "content": "Hello?", "compacted": 0, "timestamp": 1},
        {"role": "assistant", "content": "I think maybe yes",
         "reasoning_content": "let me reconsider", "compacted": 0, "timestamp": 2},
        {"role": "session_meta", "content": "", "compacted": 1, "timestamp": 3},
    ]))


@pytest.mark.parametrize("source,entries,min_turns", [
    ("reasonix", [[{"role": "system", "content": "sys"}],
                  [{"role": "user", "content": "hello"},
                   {"role": "assistant", "content": "hi"}]], 3),
    ("omp", [{"type": "title", "title": ""},
             {"type": "assistant", "id": "m1", "content": "let me check"},
             {"type": "user", "id": "m2", "content": "ok"},
             {"type": "model_change", "model": "x"}], 2),
    ("claude", [{"type": "queue-operation", "operation": "enqueue", "content": "q"},
                {"type": "assistant", "message": {"id": "msg-1",
                 "content": [{"type": "text", "text": "plan:"}, {"type": "text", "text": "go"}]}},
                {"type": "user", "message": {"id": "msg-2", "content": "ok"}}], 2),
    ("codex", [{"type": "user", "message": {"id": "u1", "content": "build it"}},
               {"type": "assistant", "message": {"id": "a1", "content": "done"}}], 2),
    ("kimi", [{"role": "_system_prompt", "content": "sys"},
              {"role": "user", "content": "hi"},
              {"role": "assistant", "content": "hello!"},
              {"role": "_checkpoint", "id": 0}], 2),
])
def test_all_sources_render(source, entries, min_turns):
    turns = render_turns(source, _lines(source, entries))
    assert len(turns) >= min_turns
    render, chunks = build_chunks(turns, target_chars=80000)
    assert len(chunks) >= 1
    resp = build_response(source, "s.jsonl", turns, render, chunks)
    assert resp["fidelity_class"] in ("full", "lossy")
    assert resp["stats"]["turn_count"] == len(turns)
    assert resp["render_sha256"] == sha256_text(render)
    assert resp["render_id"].startswith(f"{source}-s.jsonl-")


def test_hermes_render_keeps_reasoning():
    turns = _turns()
    assert turns[1]["reasoning"] == "let me reconsider"
    assert len(turns) == 2  # session_meta skipped
    text = turn_text(turns[1])
    assert "> reasoning: let me reconsider" in text
    assert "[e2][assistant]" in text


def test_deterministic_render_identical_across_runs():
    a = render_turns("hermes", _lines("hermes", [
        {"role": "user", "content": "Hello?", "compacted": 0, "timestamp": 1},
        {"role": "assistant", "content": "I think maybe yes", "compacted": 0, "timestamp": 2}]))
    b = render_turns("hermes", _lines("hermes", [
        {"role": "user", "content": "Hello?", "compacted": 0, "timestamp": 1},
        {"role": "assistant", "content": "I think maybe yes", "compacted": 0, "timestamp": 2}]))
    assert a == b
    ra, ca = build_chunks(a, 80000)
    rb, cb = build_chunks(b, 80000)
    assert ra == rb and ca == cb


def test_reasonix_dedup_repeated_append_blocks():
    # reasonix is an append-only archive: later events re-emit the cumulative
    # message list, so the same turns appear multiple times. They must collapse
    # to a single copy (no double-counting for downstream passes).
    entries = [
        [{"role": "system", "content": "sys"},
         {"role": "user", "content": "install the skill"},
         {"role": "assistant", "content": "let me check the script"}],
        # append: re-emits the same list + one new turn
        [{"role": "system", "content": "sys"},
         {"role": "user", "content": "install the skill"},
         {"role": "assistant", "content": "let me check the script"},
         {"role": "assistant", "content": "it failed on a read-only mount"}],
    ]
    turns = render_turns("reasonix", _lines("reasonix", entries))
    contents = [t["content"] for t in turns]
    assert len(turns) == 4, contents          # sys, user, 'let me check', 'it failed'
    assert contents.count("let me check the script") == 1
    assert contents.count("install the skill") == 1
    assert "it failed on a read-only mount" in contents


def test_reasonix_distinct_repeated_messages_kept():
    # genuine repeats of DIFFERENT content are not collapsed (dedup is exact)
    entries = [
        [{"role": "user", "content": "retry it"}],
        [{"role": "user", "content": "retry it"}, {"role": "assistant", "content": "done"}],
    ]
    turns = render_turns("reasonix", _lines("reasonix", entries))
    # 'retry it' appears in both cumulative lists -> collapses; 'done' added
    assert [t["content"] for t in turns] == ["retry it", "done"]


def test_turn_integrity_no_turn_split():
    turns = [{"event_id": f"t{i}", "role": "assistant", "content": "x" * 6000}
             for i in range(5)]
    render, chunks = build_chunks(turns, target_chars=10000)
    assert len(chunks) >= 2
    for t in turns:
        text = turn_text(t)
        hits = [c for c in chunks if text in render[c["char_offset"]:c["char_offset"] + c["char_length"]]]
        assert len(hits) == 1, f"turn {t['event_id']} split or missing"


def test_chunk_coverage_strict_partition():
    turns = [{"event_id": f"t{i}", "role": "user", "content": "y" * 3000} for i in range(7)]
    render, chunks = build_chunks(turns, target_chars=10000)
    assert chunks[0]["char_offset"] == 0
    for a, b in zip(chunks, chunks[1:]):
        assert b["char_offset"] == a["char_offset"] + a["char_length"]
    assert chunks[-1]["char_offset"] + chunks[-1]["char_length"] == len(render)
    joined = "".join(render[c["char_offset"]:c["char_offset"] + c["char_length"]] for c in chunks)
    assert joined == render
    all_ids = [tid for c in chunks for tid in c["turn_ids"]]
    assert len(all_ids) == len(set(all_ids)) == len(turns)
    assert all_ids == [t["event_id"] for t in turns]


def test_stable_chunk_map_shape_contiguous():
    turns = [{"event_id": f"t{i}", "role": "assistant", "content": "z" * 2500} for i in range(4)]
    render, chunks = build_chunks(turns, target_chars=10000)
    for c in chunks:
        assert set(c) == {"chunk_id", "turn_ids", "char_offset", "char_length", "sha256", "est_tokens"}
        assert c["est_tokens"] >= 1 and c["char_length"] >= 1


def test_unique_chunk_ids_in_order():
    turns = [{"event_id": f"t{i}", "role": "user", "content": "q" * 3000} for i in range(7)]
    _, chunks = build_chunks(turns, target_chars=10000)
    ids = [c["chunk_id"] for c in chunks]
    assert ids == sorted(set(ids)) == [f"c{i}" for i in range(len(chunks))]


def test_chunk_sha256_matches_slice():
    turns = [{"event_id": f"t{i}", "role": "user", "content": "w" * 3000} for i in range(7)]
    render, chunks = build_chunks(turns, target_chars=10000)
    for c in chunks:
        assert c["sha256"] == sha256_text(render[c["char_offset"]:c["char_offset"] + c["char_length"]])


def test_response_shape():
    turns = _turns()
    render, chunks = build_chunks(turns, 80000)
    resp = build_response("hermes", "s.jsonl", turns, render, chunks)
    assert set(resp) == {"render_id", "source", "filename", "fidelity_class",
                         "chunks", "render_sha256", "stats"}
    assert resp["stats"] == {"turn_count": 2, "chunk_count": 1, "total_chars": len(render)}


def test_invalid_source_rejected():
    with pytest.raises(ValueError):
        render_turns("nope", ["{}"])


def test_malformed_line_skipped_not_fatal():
    turns = render_turns("hermes", ["NOT JSON", "[]"])
    assert turns == []
