"""Candidate tests for the topic-flow-review bundle (must invariants)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topic_flow_review import PASS_ID, run  # noqa: E402
from transcript_chunking import run as chunk_run  # noqa: E402
from uncertainty_pass import run as unc_run  # noqa: E402
from topic_transition_pass import run as tt_run  # noqa: E402
from router import StubRouter  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"

_TURNS = [
    {"role": "user", "content": "Let's fix the cache invalidation bug."},
    {"role": "assistant", "content": "I'll add a version key to the cache entries and bump it on writes. " + "x" * 6000},
    {"role": "user", "content": "Now the login flow is slow. That's not what I asked for."},
    {"role": "assistant", "content": "The login handler re-fetches the profile every call; cache it. " + "y" * 6000},
]


@pytest.fixture
def prepared_store(tmp_path):
    """Archive + chunked render + topic/transition/contention layers in store."""
    archive = tmp_path / "archive"
    (archive / SRC).mkdir(parents=True)
    with open(archive / SRC / FN, "w", encoding="utf-8") as f:
        for i, t in enumerate(_TURNS, start=1):
            f.write(json.dumps({"session_id": "x", **t, "compacted": 0, "timestamp": i}) + "\n")
    store = str(tmp_path / "store")
    resp = chunk_run(SRC, FN, str(archive), target_chars=10000, chunk_store=str(store) + "/chunk-store")
    tt_stub = StubRouter(replies={r".*": {
        "topics": [
            {"label": "cache invalidation", "intensity": 0.8, "quotes": ["I'll add a version key"]},
            {"label": "login performance", "intensity": 0.6, "quotes": ["The login handler re-fetches"]},
        ],
        "transitions": [
            {"from_label": "cache invalidation", "to_label": "login performance",
             "type": "contiguous", "signal_text": "Now the login flow is slow."},
        ],
    }})
    tt_run(SRC, FN, render_id=resp["render_id"], store_dir=store,
           chunk_store=str(store) + "/chunk-store", router=tt_stub)
    unc_run(SRC, FN, render_id=resp["render_id"], store_dir=store,
            chunk_store=str(store) + "/chunk-store", lexicon_version_="")
    return store, resp["render_id"]


def _review(store, render_id, router=None, **kw):
    return run(SRC, FN, render_id=render_id, store_dir=store,
               chunk_store=str(store) + "/chunk-store", router=router, **kw)


def _good_router(prepared_store):
    from annotation_store import AnnotationStore
    s = AnnotationStore(prepared_store)
    q = s.query(SRC, FN, layer="topic")
    topic_ids = [r["annotation_id"] for r in q["records"]]
    qc = s.query(SRC, FN, layer="contention")
    cont_ids = [r["annotation_id"] for r in qc["records"]]
    reply = {
        "narrative": "The session moves from cache invalidation to login performance.",
        "findings": [
            {"kind": "tension", "title": "user pushed back on the cache plan",
             "rationale": "contention markers on the user turn",
             "supporting_refs": [{"layer": "contention", "annotation_id": cont_ids[0]}]},
            {"kind": "case-study-candidate", "title": "two-topic session",
             "rationale": "clear topic pair",
             "supporting_refs": [{"layer": "topic", "annotation_id": topic_ids[0]}]},
        ],
    }
    return StubRouter(replies={r".*": reply})


def test_response_schema_conformance(prepared_store):
    from common import bundle_schema_path, validate_against_schema
    store, rid = prepared_store
    resp = _review(store, rid, router=_good_router(store))
    assert validate_against_schema(resp, bundle_schema_path(PASS_ID, "response.schema.json")) == []
    assert resp["pass_id"] == PASS_ID
    assert resp["records_sha256"]


def test_flow_edges_derived_from_transitions(prepared_store):
    store, rid = prepared_store
    resp = _review(store, rid, router=_good_router(store))
    edges = resp["flow"]["intra_session_edges"]
    assert edges, "flow edges must be derived from transitions"
    for e in edges:
        assert e["relation"] in ("revival", "overlap", "nested", "sequential")
        assert e["from_topic_id"].startswith("t") and e["to_topic_id"].startswith("t")


def test_relation_type_enum(prepared_store):
    store, rid = prepared_store
    resp = _review(store, rid, router=_good_router(store))
    assert all(e["relation"] in ("revival", "overlap", "nested", "sequential")
               for e in resp["flow"]["intra_session_edges"])


def test_findings_grounded_refs_exist(prepared_store):
    store, rid = prepared_store
    resp = _review(store, rid, router=_good_router(store))
    assert resp["findings"]
    for f in resp["findings"]:
        assert f["supporting_refs"], f"finding {f['finding_id']} has no valid refs"
        for ref in f["supporting_refs"]:
            assert ref["layer"] in ("topic", "transition", "contention")
            assert ref["annotation_id"]


def test_invalid_refs_dropped(prepared_store):
    store, rid = prepared_store
    bad = StubRouter(replies={r".*": {
        "narrative": "x",
        "findings": [
            {"kind": "observation", "title": "phantom", "rationale": "r",
             "supporting_refs": [{"layer": "topic", "annotation_id": "nonexistent-000000000"}]},
        ],
    }})
    resp = _review(store, rid, router=bad)
    # phantom finding dropped; mechanical fallback keeps findings grounded
    assert all(ref["annotation_id"] != "nonexistent-000000000"
               for f in resp["findings"] for ref in f["supporting_refs"])
    assert resp["findings"]


def test_records_append_to_store(prepared_store):
    store, rid = prepared_store
    _review(store, rid, router=_good_router(store))
    from annotation_store import AnnotationStore
    s = AnnotationStore(store)
    q = s.query(SRC, FN, layer="topic-flow")
    assert q["total_matches"] >= 1


def test_network_router_only(prepared_store):
    store, rid = prepared_store
    stub = _good_router(store)
    _review(store, rid, router=stub)
    assert stub.calls
    import topic_flow_review as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "urllib" not in src and "requests" not in src and "socket" not in src


def test_router_calls_bounded(prepared_store):
    store, rid = prepared_store
    stub = _good_router(store)
    _review(store, rid, router=stub)
    assert len(stub.calls) == 1  # one review call per session


def test_tokens_reported(prepared_store):
    store, rid = prepared_store
    resp = _review(store, rid, router=_good_router(store))
    assert resp["tokens_in"] > 0 and resp["tokens_out"] > 0


def test_archive_untouched(prepared_store, tmp_path):
    store, rid = prepared_store
    before = open(os.path.join(tmp_path, "archive", SRC, FN), "rb").read()
    _review(store, rid, router=_good_router(store))
    assert open(os.path.join(tmp_path, "archive", SRC, FN), "rb").read() == before
