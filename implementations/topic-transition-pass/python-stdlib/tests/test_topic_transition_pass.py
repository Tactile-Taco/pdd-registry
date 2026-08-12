"""Candidate tests for the topic-transition-pass bundle (must invariants)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topic_transition_pass import PASS_ID, run  # noqa: E402
from transcript_chunking import run as chunk_run  # noqa: E402
from router import StubRouter  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"

# two chunks so transitions have somewhere to go: content sized to force 2 chunks
_TURNS = [
    {"role": "user", "content": "Let's fix the cache invalidation bug."},
    {"role": "assistant", "content": "I'll add a version key to the cache entries and bump it on writes. " + "x" * 6000},
    {"role": "user", "content": "Now the login flow is slow."},
    {"role": "assistant", "content": "The login handler re-fetches the profile every call; cache it. " + "y" * 6000},
]


@pytest.fixture
def archive_and_store(tmp_path):
    archive = tmp_path / "archive"
    (archive / SRC).mkdir(parents=True)
    with open(archive / SRC / FN, "w", encoding="utf-8") as f:
        for i, t in enumerate(_TURNS, start=1):
            f.write(json.dumps({"session_id": "x", **t, "compacted": 0, "timestamp": i}) + "\n")
    chunk_run(SRC, FN, str(archive), target_chars=10000, chunk_store=str(tmp_path / "store" / "chunk-store"))
    return str(archive), str(tmp_path / "store")


def _render_id(tmp_path):
    with open(os.path.join(str(tmp_path / "store" / "chunk-store" / SRC), FN + ".chunkmap.json"), encoding="utf-8") as f:
        return json.load(f)["render_id"]


def _stub_topics(extra: dict | None = None):
    reply = {
        "topics": [
            {"label": "cache invalidation", "intensity": 0.8,
             "quotes": ["I'll add a version key to the cache entries"]},
            {"label": "login performance", "intensity": 0.6,
             "quotes": ["The login handler re-fetches the profile"]},
        ],
        "transitions": [
            {"from_label": "cache invalidation", "to_label": "login performance",
             "type": "contiguous", "signal_text": "Now the login flow is slow."},
        ],
    }
    if extra:
        reply.update(extra)
    return StubRouter(replies={r".*": reply})


def _run_pass(store_dir, tmp_path, **kw):
    return run(SRC, FN, render_id=_render_id(tmp_path), store_dir=store_dir,
               chunk_store=str(tmp_path / "store" / "chunk-store"), **kw)


def test_response_schema_conformance(archive_and_store, tmp_path):
    from common import bundle_schema_path, validate_against_schema
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics())
    assert validate_against_schema(resp, bundle_schema_path(PASS_ID, "response.schema.json")) == []
    assert resp["pass_id"] == PASS_ID
    assert resp["tokens_in"] > 0 and resp["tokens_out"] > 0
    assert resp["records_sha256"]


def test_topic_ids_unique_and_ordered(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics())
    ids = [t["topic_id"] for t in resp["topics"]]
    assert ids == sorted(set(ids))
    assert all(tid.startswith("t") for tid in ids)
    assert ids == [f"t{i}" for i in range(len(ids))]


def test_invalid_transition_type_rejected(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    bad = _stub_topics({"transitions": [
        {"from_label": "cache invalidation", "to_label": "login performance",
         "type": "quantum", "signal_text": "..."}]})
    with pytest.raises(RuntimeError):
        _run_pass(store, tmp_path, router=bad)


def test_transition_connectivity(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics())
    ids = {t["topic_id"] for t in resp["topics"]}
    for tr in resp["transitions"]:
        assert tr["from_topic_id"] in ids
        assert tr["to_topic_id"] in ids
        assert tr["type"] in ("contiguous", "revival", "overlap", "nested")
        assert tr["position"].startswith("c")


def test_topics_cover_assigned_chunks(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics())
    covered = {c for t in resp["topics"] for c in t["chunks"]}
    with open(os.path.join(str(tmp_path / "store" / "chunk-store" / SRC), FN + ".chunkmap.json"), encoding="utf-8") as f:
        all_chunks = {c["chunk_id"] for c in json.load(f)["chunks"]}
    assert covered == all_chunks  # stub annotates every chunk
    for t in resp["topics"]:
        assert set(t["chunks"]) <= all_chunks


def test_label_stability_existing_labels_reused(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    # stub returns a case-variant; existing label must win verbatim
    stub = _stub_topics({"topics": [
        {"label": "CACHE INVALIDATION", "intensity": 0.8, "quotes": []},
        {"label": "login performance", "intensity": 0.6, "quotes": []},
    ]})
    resp = _run_pass(store, tmp_path, router=stub,
                     existing_labels=["cache invalidation"])
    labels = {t["label"] for t in resp["topics"]}
    assert "cache invalidation" in labels
    assert "CACHE INVALIDATION" not in labels


def test_span_in_bounds(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics())
    with open(os.path.join(str(tmp_path / "store" / "chunk-store" / SRC), FN + ".chunkmap.json"), encoding="utf-8") as f:
        total = max(c["char_offset"] + c["char_length"] for c in json.load(f)["chunks"])
    for t in resp["topics"]:
        assert 0 <= t["span_start"] <= t["span_end"] <= total


def test_records_append_to_store(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    _run_pass(store, tmp_path, router=_stub_topics())
    from annotation_store import AnnotationStore
    s = AnnotationStore(str(tmp_path / "store"))
    qt = s.query(SRC, FN, layer="topic")
    qr = s.query(SRC, FN, layer="transition")
    assert qt["total_matches"] >= 2
    assert qr["total_matches"] >= 1


def test_network_router_only(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    stub = _stub_topics()
    _run_pass(store, tmp_path, router=stub)
    assert stub.calls  # the router is the only network surface
    import topic_transition_pass as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "urllib" not in src and "requests" not in src and "socket" not in src
    assert "urlopen" not in src


def test_router_calls_bounded_per_chunk(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    stub = _stub_topics()
    _run_pass(store, tmp_path, router=stub)
    # exactly one model call per requested chunk
    with open(os.path.join(str(tmp_path / "store" / "chunk-store" / SRC), FN + ".chunkmap.json"), encoding="utf-8") as f:
        n_chunks = len(json.load(f)["chunks"])
    assert len(stub.calls) == n_chunks


def test_tokens_reported(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics())
    assert resp["tokens_in"] >= 0 and resp["tokens_out"] >= 0


def test_archive_untouched(archive_and_store, tmp_path):
    archive, store = archive_and_store
    before = open(os.path.join(archive, SRC, FN), "rb").read()
    _run_pass(store, tmp_path, router=_stub_topics())
    assert open(os.path.join(archive, SRC, FN), "rb").read() == before


def test_chunk_ids_subset_requested(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_pass(store, tmp_path, router=_stub_topics(), chunk_ids=["c0"])
    assert all(t["chunks"] == ["c0"] for t in resp["topics"])
