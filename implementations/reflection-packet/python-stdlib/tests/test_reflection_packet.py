"""Candidate tests for the reflection-packet bundle (must invariants)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reflection_packet import PASS_ID, build  # noqa: E402
from transcript_chunking import run as chunk_run  # noqa: E402
from uncertainty_pass import run as unc_run  # noqa: E402
from topic_transition_pass import run as tt_run  # noqa: E402
from topic_flow_review import run as fw_run  # noqa: E402
from router import StubRouter  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"

_TURNS = [
    {"role": "user", "content": "Let's fix the cache invalidation bug."},
    {"role": "assistant", "content": "I'll add a version key to the cache entries and bump it on writes. " + "x" * 6000},
    {"role": "user", "content": "Now the login flow is slow. That's not what I asked for."},
    {"role": "assistant", "content": "The login handler re-fetches the profile every call; cache it. " + "y" * 6000},
]

_TT_REPLY = {
    "topics": [
        {"label": "cache invalidation", "intensity": 0.8, "quotes": ["I'll add a version key"]},
        {"label": "login performance", "intensity": 0.6, "quotes": ["The login handler re-fetches"]},
    ],
    "transitions": [
        {"from_label": "cache invalidation", "to_label": "login performance",
         "type": "contiguous", "signal_text": "Now the login flow is slow."},
    ],
}


def _prepared(tmp_path, source=SRC, fn=FN, turns=None):
    archive = tmp_path / "archive"
    (archive / source).mkdir(parents=True)
    with open(archive / source / fn, "w", encoding="utf-8") as f:
        for i, t in enumerate(turns or _TURNS, start=1):
            if source == "claude":
                rec = {"type": t["role"], "message": {
                    "id": f"msg-{i}",
                    "content": [{"type": "text", "text": t["content"]}]}}
            else:
                rec = {"session_id": "x", **t, "compacted": 0, "timestamp": i}
            f.write(json.dumps(rec) + "\n")
    store = str(tmp_path / "store")
    resp = chunk_run(source, fn, str(archive), target_chars=10000, chunk_store=str(store) + "/chunk-store")
    tt_run(source, fn, render_id=resp["render_id"], store_dir=store,
           chunk_store=str(store) + "/chunk-store",
           router=StubRouter(replies={r".*": _TT_REPLY}))
    unc_run(source, fn, render_id=resp["render_id"], store_dir=store,
            chunk_store=str(store) + "/chunk-store", lexicon_version_="")
    # flow review with a router that references the real topic annotation ids
    from annotation_store import AnnotationStore
    s = AnnotationStore(store)
    tids = [r["annotation_id"] for r in s.query(source, fn, layer="topic")["records"]]
    cids = [r["annotation_id"] for r in s.query(source, fn, layer="contention")["records"]]
    fw_stub = StubRouter(replies={r".*": {
        "narrative": "Cache work then login perf.",
        "findings": [
            {"kind": "tension", "title": "user pushed back", "rationale": "r",
             "supporting_refs": [{"layer": "contention", "annotation_id": cids[0]}]},
            {"kind": "case-study-candidate", "title": "two-topic session", "rationale": "r",
             "supporting_refs": [{"layer": "topic", "annotation_id": tids[0]}]},
        ],
    }})
    fw_run(source, fn, render_id=resp["render_id"], store_dir=store,
           chunk_store=str(store) + "/chunk-store", router=fw_stub)
    return store, resp["render_id"]


def _build_packet(store, render_id, source=SRC, fn=FN, **kw):
    return build(source, fn, render_id=render_id, store_dir=store, **kw)


def test_response_schema_conformance(tmp_path):
    from common import bundle_schema_path, validate_against_schema
    store, rid = _prepared(tmp_path)
    resp = _build_packet(store, rid)
    assert validate_against_schema(resp, bundle_schema_path(PASS_ID, "response.schema.json")) == []
    assert resp["packet_id"].startswith("packet-")
    assert resp["packet_sha256"]


def test_deterministic_build(tmp_path):
    store, rid = _prepared(tmp_path)
    a = _build_packet(store, rid)
    b = _build_packet(store, rid)
    assert a == b
    assert a["packet_sha256"] == b["packet_sha256"]


def test_provenance_complete(tmp_path):
    store, rid = _prepared(tmp_path)
    resp = _build_packet(store, rid)
    pass_ids = {p["pass_id"] for p in resp["packet"]["provenance"]["passes"]}
    assert {"uncertainty-pass", "topic-transition-pass", "topic-flow-review"} <= pass_ids
    assert resp["packet"]["overview"]["turn_count"] == 4
    assert resp["packet"]["overview"]["chunk_count"] >= 2


def test_tension_and_case_study_from_findings(tmp_path):
    store, rid = _prepared(tmp_path)
    resp = _build_packet(store, rid)
    assert resp["packet"]["tension_summary"], "tension summary from findings"
    assert resp["packet"]["case_study_candidates"], "case-study candidates from findings"
    assert resp["packet"]["topic_flow"]["narrative"]
    assert resp["packet"]["topic_flow"]["edges"]


def test_heatmap_matrix_shape(tmp_path):
    store, rid = _prepared(tmp_path)
    resp = _build_packet(store, rid)
    hm = resp["packet"]["heatmap"]
    assert hm["matrix"]["rows"] == [f"c{i}" for i in range(len(hm["matrix"]["rows"]))]
    assert hm["matrix"]["columns"] == ["uncertainty-density", "contention-count", "topic-count"]
    assert len(hm["matrix"]["cells"]) == len(hm["matrix"]["rows"])
    assert all(len(row) == 3 for row in hm["matrix"]["cells"])
    assert all(v is None or isinstance(v, (int, float)) for row in hm["matrix"]["cells"] for v in row)
    assert hm["matrix"]["normalization"] in ("baseline-deviation", "raw")
    assert "<table>" in hm["render"] and hm["render"].startswith("chunk")


def test_lossy_fidelity_null_cells(tmp_path):
    store, rid = _prepared(tmp_path, source="claude", fn="c.jsonl")
    resp = _build_packet(store, rid, source="claude", fn="c.jsonl")
    assert resp["packet"]["session"]["fidelity_class"] == "lossy"
    cells = resp["packet"]["heatmap"]["matrix"]["cells"]
    assert cells and all(v is None for row in cells for v in row)
    assert "lossy" in resp["packet"]["overview"]["fidelity_note"]


def test_derived_only_no_model_calls():
    import reflection_packet as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "urllib" not in src and "requests" not in src and "socket" not in src
    assert "complete_json" not in src and "router" not in src


def test_no_raw_transcript_content(tmp_path):
    store, rid = _prepared(tmp_path)
    resp = _build_packet(store, rid)
    blob = json.dumps(resp)
    assert "I'll add a version key" not in blob
    assert "Now the login flow is slow" not in blob
    assert '"content"' not in blob


def test_packet_output_only(tmp_path):
    store, rid = _prepared(tmp_path)
    out = tmp_path / "packets"
    _build_packet(store, rid, out_dir=str(out))
    files = list(out.iterdir())
    assert len(files) == 1 and files[0].name.endswith(".packet.json")
    with open(files[0], encoding="utf-8") as f:
        assert json.load(f)["packet_id"].startswith("packet-")
