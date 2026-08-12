"""Candidate tests for topic-graph (pure core)."""

from __future__ import annotations

import hashlib
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topic_graph import TopicGraphCore, jaccard, session_key  # noqa: E402


def _topics(labels):
    return [{"topic_id": f"t{i}", "label": label, "intensity": 0.5}
            for i, label in enumerate(labels)]


def test_response_shape():
    g = TopicGraphCore(edge_threshold=0.7)
    resp = g.add_session("hermes", "sess-a.jsonl", _topics(["cache invalidation"]))
    assert set(resp) == {"graph_version", "added_nodes", "added_edges", "edges",
                         "index_size", "index_sha256"}
    assert resp["graph_version"] >= 1
    assert resp["index_size"] >= 1
    assert re.fullmatch(r"[0-9a-f]{64}", resp["index_sha256"])


def test_incremental_add_idempotent():
    g = TopicGraphCore(edge_threshold=0.7)
    a = g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation", "login flow"]))
    assert a["added_nodes"] == 2
    v1 = a["graph_version"]
    b = g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation", "login flow"]))
    assert b["added_nodes"] == 0 and b["added_edges"] == 0
    assert b["graph_version"] == v1
    c = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    assert c["added_nodes"] == 1 and c["graph_version"] > v1


def test_cross_session_similar_edges():
    g = TopicGraphCore(edge_threshold=0.5)
    g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    resp = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    assert resp["added_edges"] >= 1
    assert any(e["type"] == "similar" and e["similarity"] == 1.0 for e in resp["edges"])
    g2 = TopicGraphCore(edge_threshold=0.9)
    g2.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    r2 = g2.add_session("omp", "s2.jsonl", _topics(["login performance"]))
    assert r2["added_edges"] == 0


def test_node_ids_unique_pattern():
    g = TopicGraphCore()
    g.add_session("hermes", "my session.jsonl", _topics(["a", "b", "a b"]))
    ids = list(g.nodes)
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9-]+::t[0-9]+", nid) for nid in ids)
    assert session_key("hermes", "my session.jsonl") == "hermes-my-session"


def test_index_size_is_nodes_plus_edges():
    g = TopicGraphCore(edge_threshold=0.4)
    resp = g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation", "cache eviction"]))
    assert resp["index_size"] == len(g.nodes) + len(g.edges)


def test_similarity_bounds():
    assert 0.0 <= jaccard({"a", "b"}, {"c"}) <= 1.0
    assert jaccard({"a"}, {"a"}) == 1.0
    assert jaccard(set(), set()) == 0.0
    g = TopicGraphCore(edge_threshold=0.0)
    g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    resp = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    for e in resp["edges"]:
        assert 0.0 <= e["similarity"] <= 1.0


def test_embedding_local_deterministic():
    a = TopicGraphCore(edge_threshold=0.5)
    b = TopicGraphCore(edge_threshold=0.5)
    labels = ["cache invalidation", "login performance", "deploy pipeline"]
    ra = a.add_session("hermes", "s1.jsonl", _topics(labels))
    rb = b.add_session("hermes", "s1.jsonl", _topics(labels))
    assert ra["edges"] == rb["edges"]
    assert ra["index_sha256"] == rb["index_sha256"]


def test_edge_typing_and_targets():
    g = TopicGraphCore(edge_threshold=0.5)
    g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    resp = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    node_ids = set(g.nodes)
    for e in resp["edges"]:
        assert e["type"] in ("similar", "revival", "overlap")
        assert e["from_node_id"] in node_ids and e["to_node_id"] in node_ids
        assert e["from_node_id"] != e["to_node_id"]


def test_migration_threshold_observed():
    g = TopicGraphCore(edge_threshold=0.0, migration_threshold=2)
    g.add_session("hermes", "s1.jsonl", _topics(["a"]))
    g.add_session("omp", "s2.jsonl", _topics(["b"]))
    g.add_session("claude", "s3.jsonl", _topics(["c"]))
    assert g.migration_hits >= 1
    assert g.migration_log


def test_no_llm_reasoning():
    import topic_graph as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("urllib", "requests", "socket", "open(", "os.", "complete_json", "router"):
        assert banned not in src
    assert "invalid_request" in src


def test_hash_matches_hashlib():
    import _hash as h
    assert h.sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()
