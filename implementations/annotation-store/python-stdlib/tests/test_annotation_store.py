"""Candidate tests for annotation-store (pure core; in-memory fixtures)."""

from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from annotation_store import AnnotationCore  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"
CHUNKS = {"c0", "c1"}


def _mk(annotation_id="ann-00000001", layer="test", kind="probe",
        event_id="e1", revision=1, marker_text=None, chunk_id=None):
    payload = {}
    if marker_text is not None:
        payload["marker_text"] = marker_text
    target = {"source": SRC, "filename": FN}
    if chunk_id is not None:
        target["chunk_id"] = chunk_id
    else:
        target["event_id"] = event_id
    return {"annotation_id": annotation_id, "layer": layer, "kind": kind,
            "target": target, "revision": revision, "payload": payload,
            "created_at": "2026-08-12T00:00:00Z"}


def _turns():
    return [
        {"event_id": "e1", "role": "user", "content": "Hello?", "reasoning": ""},
        {"event_id": "e2", "role": "assistant", "content": "I think maybe yes",
         "reasoning": "let me reconsider"},
    ]


def _chunk_map():
    return {"chunks": [{"chunk_id": "c0", "turn_ids": ["e1", "e2"]},
                       {"chunk_id": "c1", "turn_ids": ["e3"]}]}


def test_append_query_roundtrip():
    core = AnnotationCore()
    resp = core.append("uncertainty-pass", "0.1.0", [_mk()], chunk_ids=CHUNKS)
    assert resp["accepted_count"] == 1 and resp["superseded_count"] == 0
    assert resp["store_sha256"]
    q = core.query(SRC, FN)
    assert q["total_matches"] == 1
    assert q["records"][0]["pass_id"] == "uncertainty-pass"


def test_supersede_visibility_highest_revision_wins():
    core = AnnotationCore()
    core.append("p", "1", [_mk(revision=1)], chunk_ids=CHUNKS)
    resp2 = core.append("p", "1", [_mk(revision=2)], chunk_ids=CHUNKS)
    assert resp2["superseded_count"] == 1
    q = core.query(SRC, FN)
    assert q["total_matches"] == 2
    assert len(q["records"]) == 1 and q["records"][0]["revision"] == 2


def test_append_only_no_mutation_no_deletion():
    core = AnnotationCore()
    core.append("p", "1", [_mk(revision=1)], chunk_ids=CHUNKS)
    core.append("p", "1", [_mk(revision=2)], chunk_ids=CHUNKS)
    assert len(core.records) == 2  # both persisted; nothing mutated/deleted


def test_unique_annotation_ids_enforced():
    core = AnnotationCore()
    with pytest.raises(ValueError):
        core.append("p", "1", [_mk("ann-00000002"), _mk("ann-00000002")], chunk_ids=CHUNKS)
    core.append("p", "1", [_mk("ann-00000001")], chunk_ids=CHUNKS)
    with pytest.raises(ValueError):
        core.append("p", "1", [_mk("ann-00000001", event_id="e2")], chunk_ids=CHUNKS)


def test_address_integrity_chunk_resolution():
    core = AnnotationCore()
    core.append("p", "1", [_mk("ann-00000001", chunk_id="c0")], chunk_ids=CHUNKS)
    with pytest.raises(ValueError):
        core.append("p", "1", [_mk("ann-00000002", chunk_id="c99")], chunk_ids=CHUNKS)
    # missing both event_id and chunk_id → invalid
    with pytest.raises(ValueError):
        core.append("p", "1", [{
            "annotation_id": "ann-00000003", "layer": "t", "kind": "k",
            "target": {"source": SRC, "filename": FN},
            "revision": 1, "payload": {}, "created_at": "2026-08-12T00:00:00Z"}],
            chunk_ids=CHUNKS)


def test_bounded_batch_rejects_oversize():
    core = AnnotationCore()
    with pytest.raises(ValueError):
        core.append("p", "1", [_mk(f"ann-{i:08d}") for i in range(10001)], chunk_ids=CHUNKS)
    with pytest.raises(ValueError):
        core.append("p", "1", [], chunk_ids=CHUNKS)


def test_render_deterministic_and_markers_applied():
    core = AnnotationCore()
    core.append("uncertainty-pass", "0.1.0", [
        _mk("ann-00000001", layer="uncertainty", kind="marker-span", event_id="e1"),
        _mk("ann-00000002", layer="contention", kind="contention-event", event_id="e1"),
        _mk("ann-00000003", layer="uncertainty", kind="marker-span", event_id="e2",
            marker_text="!! UNSURE"),
    ], chunk_ids=CHUNKS)
    a = core.render(_turns(), _chunk_map(), ["uncertainty", "contention"], "bracketed")
    b = core.render(_turns(), _chunk_map(), ["uncertainty", "contention"], "bracketed")
    assert a == b
    assert a["applied_record_count"] == 3
    assert "[uncertainty:marker-span]" in a["stitched_text"]
    exp = core.render(_turns(), _chunk_map(), ["uncertainty", "contention"], "explicit")
    assert "!! UNSURE" in exp["stitched_text"]
    pre = core.render(_turns(), _chunk_map(), ["contention", "uncertainty"], "bracketed")
    t = pre["stitched_text"]
    assert t.index("[contention:contention-event]") < t.index("[uncertainty:marker-span]")
    # chunk_id-targeted marker lands on the chunk's first turn (e1)
    core.append("t", "1", [_mk("ann-00000004", layer="topic", kind="topic", chunk_id="c0")],
                chunk_ids=CHUNKS)
    r = core.render(_turns(), _chunk_map(), ["topic"], "bracketed")
    lines = r["stitched_text"].splitlines()
    assert lines[0] == "[e1][user]"
    assert "[topic:topic]" in lines[1]


def test_error_kinds_present_in_source():
    import annotation_store as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "invalid_request" in src
    assert "conflict" in src
