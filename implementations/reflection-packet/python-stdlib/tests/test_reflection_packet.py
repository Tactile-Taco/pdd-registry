"""Candidate tests for reflection-packet (pure core)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reflection_packet import PASS_ID, build  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"


def _fixture(lossy=False):
    source = "claude" if lossy else SRC
    chunk_map = {"chunks": [
        {"chunk_id": "c0", "turn_ids": ["e1", "e2"]},
        {"chunk_id": "c1", "turn_ids": ["e3", "e4"]},
    ]}
    turns = [{"event_id": f"e{i}", "role": "user" if i % 2 else "assistant",
              "content": f"turn {i}", "reasoning": ""} for i in range(1, 5)]
    rec = {
        "uncertainty": [{"pass_id": "uncertainty-pass", "pass_version": "0.1.0",
                         "layer": "uncertainty", "kind": "marker-span",
                         "target": {"source": source, "filename": FN, "chunk_id": "c0"},
                         "annotation_id": "u-00000001", "revision": 1,
                         "payload": {"density_per_1k": 5.0}}],
        "contention": [{"pass_id": "uncertainty-pass", "pass_version": "0.1.0",
                        "layer": "contention", "kind": "contention-event",
                        "target": {"source": source, "filename": FN, "event_id": "e1"},
                        "annotation_id": "ct-00000001", "revision": 1,
                        "payload": {"markers": ["revert"]}}],
        "topic": [{"pass_id": "topic-transition-pass", "pass_version": "0.1.0",
                   "layer": "topic", "kind": "topic",
                   "target": {"source": source, "filename": FN, "chunk_id": "c0"},
                   "annotation_id": "tp-00000001", "revision": 1,
                   "payload": {"topic_id": "t0", "label": "cache invalidation"}}],
        "transition": [{"pass_id": "topic-transition-pass", "pass_version": "0.1.0",
                        "layer": "transition", "kind": "transition",
                        "target": {"source": source, "filename": FN, "chunk_id": "c0"},
                        "annotation_id": "tr-00000001", "revision": 1,
                        "payload": {"from_topic_id": "t0", "to_topic_id": "t1",
                                    "type": "contiguous"}}],
        "topic-flow": [
            {"pass_id": "topic-flow-review", "pass_version": "0.1.0",
             "layer": "topic-flow", "kind": "finding",
             "target": {"source": source, "filename": FN, "chunk_id": "c0"},
             "annotation_id": "f-00000001", "revision": 1,
             "payload": {"kind": "tension", "title": "user pushed back"}},
            {"pass_id": "topic-flow-review", "pass_version": "0.1.0",
             "layer": "topic-flow", "kind": "flow",
             "target": {"source": source, "filename": FN, "chunk_id": "c0"},
             "annotation_id": "fl-00000001", "revision": 1,
             "payload": {"narrative": "cache then login", "edges": [
                 {"from_topic_id": "t0", "to_topic_id": "t1", "relation": "sequential"}]}},
        ],
    }
    return source, chunk_map, turns, rec


def test_response_shape():
    source, cm, turns, rec = _fixture()
    resp = build(source, FN, "rid-1", cm, turns, rec)
    assert set(resp) == {"packet_id", "packet", "packet_sha256"}
    p = resp["packet"]
    assert p["session"]["fidelity_class"] == "full"
    assert p["overview"]["turn_count"] == 4 and p["overview"]["chunk_count"] == 2
    assert p["topic_flow"]["narrative"] == "cache then login"
    assert p["tension_summary"] == ["user pushed back"]
    assert p["case_study_candidates"] == []
    assert p["provenance"]["baselines_ref"] == ""


def test_deterministic_build():
    source, cm, turns, rec = _fixture()
    a = build(source, FN, "rid-1", cm, turns, rec)
    b = build(source, FN, "rid-1", cm, turns, rec)
    assert a == b
    assert a["packet_sha256"] == b["packet_sha256"]


def test_provenance_complete():
    source, cm, turns, rec = _fixture()
    resp = build(source, FN, "rid-1", cm, turns, rec)
    pass_ids = {p["pass_id"] for p in resp["packet"]["provenance"]["passes"]}
    assert {"uncertainty-pass", "topic-transition-pass", "topic-flow-review"} <= pass_ids


def test_heatmap_matrix_shape():
    source, cm, turns, rec = _fixture()
    hm = build(source, FN, "rid-1", cm, turns, rec)["packet"]["heatmap"]
    assert hm["matrix"]["rows"] == ["c0", "c1"]
    assert hm["matrix"]["columns"] == ["uncertainty-density", "contention-count", "topic-count"]
    assert len(hm["matrix"]["cells"]) == 2 and all(len(r) == 3 for r in hm["matrix"]["cells"])
    assert hm["matrix"]["cells"][0][0] == 5.0  # density
    assert hm["matrix"]["cells"][0][1] == 1.0  # contention on e1 in c0
    assert hm["matrix"]["cells"][0][2] == 1.0  # topic
    assert hm["matrix"]["normalization"] == "raw"
    assert "<table>" in hm["render"]


def test_baseline_deviation_normalization():
    source, cm, turns, rec = _fixture()
    resp = build(source, FN, "rid-1", cm, turns, rec,
                 baselines_ref="bl", baseline_stats={"mean": 3.0, "std": 1.0})
    hm = resp["packet"]["heatmap"]
    assert hm["matrix"]["normalization"] == "baseline-deviation"
    assert hm["matrix"]["cells"][0][0] == 2.0  # (5-3)/1
    assert resp["packet"]["baseline_refs"] == ["bl"]


def test_lossy_fidelity_null_cells():
    source, cm, turns, rec = _fixture(lossy=True)
    resp = build(source, FN, "rid-1", cm, turns, rec)
    assert resp["packet"]["session"]["fidelity_class"] == "lossy"
    assert "lossy" in resp["packet"]["overview"]["fidelity_note"]
    cells = resp["packet"]["heatmap"]["matrix"]["cells"]
    assert cells and all(v is None for row in cells for v in row)


def test_no_raw_transcript_content():
    source, cm, turns, rec = _fixture()
    blob = json.dumps(build(source, FN, "rid-1", cm, turns, rec))
    assert "turn 1" not in blob and '"content"' not in blob


def test_derived_only_no_model_calls():
    import reflection_packet as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("urllib", "requests", "socket", "open(", "os.", "complete_json", "router"):
        assert banned not in src
    assert "invalid_request" in src
