"""Candidate tests for the topic-graph bundle (must invariants)."""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topic_graph import PASS_ID, TopicGraph, jaccard, run  # noqa: E402


def _topics(labels):
    return [{"topic_id": f"t{i}", "label": label, "intensity": 0.5}
            for i, label in enumerate(labels)]


def test_response_schema_conformance(tmp_path):
    from common import bundle_schema_path, validate_against_schema
    resp = run("hermes", "sess-a.jsonl", _topics(["cache invalidation"]),
               store_dir=str(tmp_path), edge_threshold=0.7)
    assert validate_against_schema(resp, bundle_schema_path(PASS_ID, "response.schema.json")) == []
    assert resp["graph_version"] >= 1
    assert resp["index_size"] >= 1


def test_incremental_add_idempotent(tmp_path):
    g = TopicGraph(str(tmp_path))
    a = g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation", "login flow"]))
    assert a["added_nodes"] == 2
    v1 = a["graph_version"]
    b = g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation", "login flow"]))
    assert b["added_nodes"] == 0 and b["added_edges"] == 0
    assert b["graph_version"] == v1  # no change, no version bump
    # a different session still adds
    c = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    assert c["added_nodes"] == 1
    assert c["graph_version"] > v1


def test_cross_session_similar_edges(tmp_path):
    g = TopicGraph(str(tmp_path), edge_threshold=0.5)
    g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    resp = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    assert resp["added_nodes"] == 1
    assert resp["added_edges"] >= 1
    sim = jaccard(_tokens("cache invalidation"), _tokens("cache invalidation"))
    assert any(e["similarity"] == sim and e["type"] == "similar" for e in resp["edges"])
    # low-similarity labels produce no edge at the default threshold
    g2 = TopicGraph(str(tmp_path / "g2"), edge_threshold=0.9)
    g2.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    r2 = g2.add_session("omp", "s2.jsonl", _topics(["login performance"]))
    assert r2["added_edges"] == 0


def _tokens(label):
    return set(re.findall(r"[a-z0-9]+", label.casefold()))


def test_node_ids_unique_pattern(tmp_path):
    g = TopicGraph(str(tmp_path))
    g.add_session("hermes", "my session.jsonl", _topics(["a", "b", "a b"]))
    ids = list(g.nodes)
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9-]+::t[0-9]+", nid) for nid in ids)
    # session key sanitized: "hermes-my-session"


def test_index_size_is_nodes_plus_edges(tmp_path):
    g = TopicGraph(str(tmp_path), edge_threshold=0.4)
    resp = g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation", "cache eviction"]))
    assert resp["index_size"] == len(g.nodes) + len(g.edges)
    assert resp["index_sha256"]


def test_similarity_bounds(tmp_path):
    g = TopicGraph(str(tmp_path), edge_threshold=0.0)
    g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    resp = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    for e in resp["edges"]:
        assert 0.0 <= e["similarity"] <= 1.0


def test_embedding_local_deterministic(tmp_path):
    a = TopicGraph(str(tmp_path / "ga"), edge_threshold=0.5)
    b = TopicGraph(str(tmp_path / "gb"), edge_threshold=0.5)
    labels = ["cache invalidation", "login performance", "deploy pipeline"]
    ra = a.add_session("hermes", "s1.jsonl", _topics(labels))
    rb = b.add_session("hermes", "s1.jsonl", _topics(labels))
    assert ra["edges"] == rb["edges"]
    assert ra["index_sha256"] == rb["index_sha256"]


def test_edge_typing_and_targets(tmp_path):
    g = TopicGraph(str(tmp_path), edge_threshold=0.5)
    g.add_session("hermes", "s1.jsonl", _topics(["cache invalidation"]))
    resp = g.add_session("omp", "s2.jsonl", _topics(["cache invalidation"]))
    node_ids = set(g.nodes)
    for e in resp["edges"]:
        assert e["type"] in ("similar", "revival", "overlap")
        assert e["from_node_id"] in node_ids
        assert e["to_node_id"] in node_ids
        assert e["from_node_id"] != e["to_node_id"]


def test_migration_threshold_observed(tmp_path):
    g = TopicGraph(str(tmp_path), edge_threshold=0.0, migration_threshold=2)
    g.add_session("hermes", "s1.jsonl", _topics(["a"]))
    g.add_session("omp", "s2.jsonl", _topics(["b"]))
    g.add_session("claude", "s3.jsonl", _topics(["c"]))
    assert g.migration_hits >= 1
    log = open(os.path.join(str(tmp_path), "topic-graph.log"), encoding="utf-8").read()
    assert "migration" in log


def test_no_llm_reasoning():
    import topic_graph as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "router" not in src and "urllib" not in src and "requests" not in src
    assert "complete_json" not in src
