"""Candidate tests for the transcript-chunking bundle (must invariants)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transcript_chunking import PASS_ID, build_chunks, run  # noqa: E402
from common import Turn, sha256_text, turn_text  # noqa: E402


# --------------------------------------------------------------------------
# fixtures: one sample archive file per source
# --------------------------------------------------------------------------

def _write(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


@pytest.fixture
def archive(tmp_path):
    src = tmp_path / "transcript-archive"
    src.mkdir()
    (src / "reasonix").mkdir()
    (src / "omp").mkdir()
    (src / "claude").mkdir()
    (src / "codex").mkdir()
    (src / "kimi").mkdir()
    (src / "hermes").mkdir()

    _write(str(src / "reasonix" / "s.jsonl"), [
        json.dumps({"schema_version": 1, "type": "replace", "revision": 1, "base_revision": 0,
                    "messages": [{"role": "system", "content": "you are a helper"}]}),
        json.dumps({"schema_version": 1, "type": "append", "revision": 2, "base_revision": 1,
                    "message_index": 1, "messages": [{"role": "user", "content": "hello"},
                                                     {"role": "assistant", "content": "hi there"}]}),
    ])
    _write(str(src / "omp" / "s.jsonl"), [
        json.dumps({"type": "title", "v": 1, "title": "", "updatedAt": "2026-08-11T12:18:40.711Z"}),
        json.dumps({"type": "session", "version": 3, "id": "abc", "timestamp": "2026-08-11T12:18:40.711Z", "cwd": "/tmp"}),
        json.dumps({"type": "assistant", "id": "m1", "content": "let me check", "model": "deepseek-v4-flash"}),
        json.dumps({"type": "user", "id": "m2", "content": "ok"}),
        json.dumps({"type": "model_change", "model": "other"}),
    ])
    _write(str(src / "claude" / "s.jsonl"), [
        json.dumps({"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-04-22T17:05:05.848Z", "sessionId": "x", "content": "queued"}),
        json.dumps({"type": "assistant", "message": {"id": "msg-1", "content": [{"type": "text", "text": "plan:"}, {"type": "text", "text": "step one"}]}}),
        json.dumps({"type": "user", "message": {"id": "msg-2", "content": [{"type": "text", "text": "go ahead"}]}}),
    ])
    _write(str(src / "codex" / "s.jsonl"), [
        json.dumps({"type": "user", "message": {"id": "u1", "content": "build it"}, "timestamp": 1}),
        json.dumps({"type": "assistant", "message": {"id": "a1", "content": "done"}, "timestamp": 2}),
    ])
    _write(str(src / "kimi" / "s.jsonl"), [
        json.dumps({"role": "_system_prompt", "content": "you are kimi"}),
        json.dumps({"role": "user", "content": "hi"}),
        json.dumps({"role": "assistant", "content": "hello!"}),
        json.dumps({"role": "_checkpoint", "id": 0}),
    ])
    _write(str(src / "hermes" / "s.jsonl"), [
        json.dumps({"session_id": "x", "role": "user", "content": "Hello?", "compacted": 0, "model": "kimi-k2.6", "timestamp": 1}),
        json.dumps({"session_id": "x", "role": "assistant", "content": "I think maybe yes", "reasoning_content": "let me reconsider", "compacted": 0, "model": "kimi-k2.6", "timestamp": 2}),
        json.dumps({"session_id": "x", "role": "session_meta", "content": "", "compacted": 1, "timestamp": 3}),
    ])
    return str(src)


ARCHIVE = str  # alias for readability


@pytest.mark.parametrize("source,fname,min_turns", [
    ("reasonix", "s.jsonl", 3),
    ("omp", "s.jsonl", 2),
    ("claude", "s.jsonl", 2),
    ("codex", "s.jsonl", 2),
    ("kimi", "s.jsonl", 2),
    ("hermes", "s.jsonl", 2),
])
def test_all_sources_render_and_conform(archive, source, fname, min_turns, tmp_path):
    resp = run(source, fname, archive, target_chars=80000,
               chunk_store=str(tmp_path / "cs"))
    assert resp["source"] == source
    assert resp["filename"] == fname
    assert resp["stats"]["turn_count"] >= min_turns
    assert resp["fidelity_class"] in ("full", "lossy")
    assert resp["stats"]["chunk_count"] >= 1
    assert resp["render_sha256"]
    # schema-conformance
    assert _schema_errors(resp) == []


def _schema_errors(resp):
    from common import bundle_schema_path, validate_against_schema
    return validate_against_schema(resp, bundle_schema_path(PASS_ID, "response.schema.json"))


# --------------------------------------------------------------------------
# deterministic-render
# --------------------------------------------------------------------------

def test_deterministic_render_identical_across_runs(archive):
    a = run("hermes", "s.jsonl", archive, target_chars=80000)
    b = run("hermes", "s.jsonl", archive, target_chars=80000)
    assert a == b
    assert a["render_sha256"] == b["render_sha256"]
    assert [c["sha256"] for c in a["chunks"]] == [c["sha256"] for c in b["chunks"]]


# --------------------------------------------------------------------------
# turn-integrity / chunk-coverage / stable-chunk-map-shape / hash-bound
# --------------------------------------------------------------------------

def test_turn_integrity_no_turn_split():
    # two turns of ~6000 chars with target 10000: turn 1+2 share a chunk,
    # turn 3 starts a new chunk — no turn is ever split.
    turns = [Turn(event_id=f"t{i}", role="assistant", content="x" * 6000) for i in range(5)]
    render, chunks = build_chunks(turns, target_chars=10000)
    assert len(chunks) >= 2
    for t in turns:
        text = turn_text(t)
        # exactly one chunk slice contains the full turn text
        hits = [c for c in chunks if text in render[c["char_offset"]:c["char_offset"] + c["char_length"]]]
        assert len(hits) == 1, f"turn {t.event_id} split or missing"


def test_chunk_coverage_strict_partition():
    turns = [Turn(event_id=f"t{i}", role="user", content="y" * 3000) for i in range(7)]
    render, chunks = build_chunks(turns, target_chars=10000)
    assert chunks[0]["char_offset"] == 0
    for a, b in zip(chunks, chunks[1:]):
        assert b["char_offset"] == a["char_offset"] + a["char_length"]
    assert chunks[-1]["char_offset"] + chunks[-1]["char_length"] == len(render)
    joined = "".join(render[c["char_offset"]:c["char_offset"] + c["char_length"]] for c in chunks)
    assert joined == render
    # every turn appears in exactly one chunk (ids disjoint, ordered)
    all_ids = [tid for c in chunks for tid in c["turn_ids"]]
    assert len(all_ids) == len(set(all_ids)) == len(turns)
    assert all_ids == [t.event_id for t in turns]


def test_stable_chunk_map_shape_contiguous():
    turns = [Turn(event_id=f"t{i}", role="assistant", content="z" * 2500) for i in range(4)]
    render, chunks = build_chunks(turns, target_chars=10000)
    for c in chunks:
        assert set(c) == {"chunk_id", "turn_ids", "char_offset", "char_length", "sha256", "est_tokens"}
    assert all(c["est_tokens"] >= 1 for c in chunks)
    assert all(c["char_length"] >= 1 for c in chunks)


def test_unique_chunk_ids_in_order():
    turns = [Turn(event_id=f"t{i}", role="user", content="q" * 3000) for i in range(7)]
    _, chunks = build_chunks(turns, target_chars=10000)
    ids = [c["chunk_id"] for c in chunks]
    assert ids == sorted(set(ids)) == [f"c{i}" for i in range(len(chunks))]


def test_chunk_sha256_matches_slice():
    turns = [Turn(event_id=f"t{i}", role="user", content="w" * 3000) for i in range(7)]
    render, chunks = build_chunks(turns, target_chars=10000)
    for c in chunks:
        assert c["sha256"] == sha256_text(render[c["char_offset"]:c["char_offset"] + c["char_length"]])


# --------------------------------------------------------------------------
# archive-read-only / no-model-calls
# --------------------------------------------------------------------------

def test_archive_bytes_unchanged(archive):
    before = {}
    for root, _dirs, files in os.walk(archive):
        for fn in files:
            p = os.path.join(root, fn)
            before[p] = open(p, "rb").read()
    for source in ("reasonix", "omp", "claude", "codex", "kimi", "hermes"):
        run(source, "s.jsonl", archive, target_chars=80000)
    for p, b in before.items():
        assert open(p, "rb").read() == b, f"archive file modified: {p}"


def test_no_model_calls_no_network():
    import transcript_chunking as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "urllib" not in src and "requests" not in src and "socket" not in src
    assert "complete_json" not in src
    # module import pulls in no network-capable stdlib at top level
    assert "import socket" not in src


def test_missing_transcript_raises(archive, tmp_path):
    with pytest.raises(FileNotFoundError):
        run("hermes", "nope.jsonl", str(tmp_path), target_chars=80000)
