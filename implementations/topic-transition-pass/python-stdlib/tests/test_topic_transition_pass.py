"""Candidate tests for topic-transition-pass (pure core; inline stub router)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topic_transition_pass import PASS_ID, run  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"


class Stub:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.last_usage = {"tokens_in": 100, "tokens_out": 40}

    def complete_json(self, prompt, system=None):
        self.calls.append(prompt)
        return self.reply


def _fixture():
    turns = [
        {"event_id": "e1", "role": "user", "content": "Let's fix the cache invalidation bug."},
        {"event_id": "e2", "role": "assistant", "content": "I'll add a version key to the cache entries." + "x" * 6000},
        {"event_id": "e3", "role": "user", "content": "Now the login flow is slow."},
        {"event_id": "e4", "role": "assistant", "content": "The login handler re-fetches the profile." + "y" * 6000},
    ]
    chunk_map = {"chunks": [
        {"chunk_id": "c0", "turn_ids": ["e1", "e2"], "char_offset": 0, "char_length": 7000},
        {"chunk_id": "c1", "turn_ids": ["e3", "e4"], "char_offset": 7000, "char_length": 7000},
    ]}
    return chunk_map, turns


def _reply(extra=None):
    r = {
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
        r.update(extra)
    return r


def test_response_shape_and_topic_ids():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns, router=Stub(_reply()))
    assert resp["pass_id"] == PASS_ID
    ids = [t["topic_id"] for t in resp["topics"]]
    assert ids == ["t0", "t1"]
    assert resp["tokens_in"] == 200 and resp["tokens_out"] == 80
    assert resp["records_sha256"]


def test_transition_connectivity_and_types():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns, router=Stub(_reply()))
    ids = {t["topic_id"] for t in resp["topics"]}
    for tr in resp["transitions"]:
        assert tr["from_topic_id"] in ids and tr["to_topic_id"] in ids
        assert tr["type"] in ("contiguous", "revival", "overlap", "nested")
        assert tr["position"] in ("c0", "c1")


def test_invalid_transition_type_raises():
    chunk_map, turns = _fixture()
    with pytest.raises((ValueError, KeyError, TypeError, AssertionError)):
        # schema-type violations surface as errors in the response contract
        resp = run(SRC, FN, chunk_map, turns, router=Stub(_reply({
            "transitions": [{"from_label": "cache invalidation",
                             "to_label": "login performance",
                             "type": "quantum", "signal_text": "..."}]})))
        for tr in resp["transitions"]:
            assert tr["type"] in ("contiguous", "revival", "overlap", "nested")


def test_label_stability_existing_labels_reused():
    chunk_map, turns = _fixture()
    stub = Stub(_reply({"topics": [
        {"label": "CACHE INVALIDATION", "intensity": 0.8, "quotes": []},
        {"label": "login performance", "intensity": 0.6, "quotes": []},
    ]}))
    resp = run(SRC, FN, chunk_map, turns, existing_labels=["cache invalidation"],
               router=stub)
    labels = {t["label"] for t in resp["topics"]}
    assert "cache invalidation" in labels and "CACHE INVALIDATION" not in labels


def test_topics_cover_assigned_chunks_and_spans():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns, router=Stub(_reply()))
    covered = {c for t in resp["topics"] for c in t["chunks"]}
    assert covered == {"c0", "c1"}
    total = max(c["char_offset"] + c["char_length"] for c in chunk_map["chunks"])
    for t in resp["topics"]:
        assert 0 <= t["span_start"] <= t["span_end"] <= total


def test_one_model_call_per_chunk_and_records():
    chunk_map, turns = _fixture()
    stub = Stub(_reply())
    resp = run(SRC, FN, chunk_map, turns, router=stub)
    assert len(stub.calls) == 2
    layers = {r["layer"] for r in resp["records"]}
    assert layers == {"topic", "transition"}


def test_chunk_ids_subset_requested():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns, router=Stub(_reply()), chunk_ids=["c0"])
    assert all(t["chunks"] == ["c0"] for t in resp["topics"])
    with pytest.raises(ValueError):
        run(SRC, FN, chunk_map, turns, router=Stub(_reply()), chunk_ids=["c9"])


def test_no_network_imports():
    import topic_transition_pass as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("urllib", "requests", "socket", "open(", "os.", "import router"):
        assert banned not in src
    assert "invalid_request" in src
