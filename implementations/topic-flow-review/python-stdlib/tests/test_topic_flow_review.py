"""Candidate tests for topic-flow-review (pure core; inline stub router)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topic_flow_review import PASS_ID, derive_flow, run  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"


class Stub:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.last_usage = {"tokens_in": 50, "tokens_out": 20}

    def complete_json(self, prompt, system=None):
        self.calls.append(prompt)
        return self.reply


def _records():
    topics = [
        {"annotation_id": "topic-00000001", "layer": "topic",
         "payload": {"topic_id": "t0", "label": "cache invalidation"}},
        {"annotation_id": "topic-00000002", "layer": "topic",
         "payload": {"topic_id": "t1", "label": "login performance"}},
    ]
    transitions = [
        {"annotation_id": "transit-00000001", "layer": "transition",
         "payload": {"from_topic_id": "t0", "to_topic_id": "t1", "type": "contiguous"}},
    ]
    contention = [
        {"annotation_id": "contention-00000001", "layer": "contention",
         "payload": {"markers": ["revert"]}},
    ]
    return topics, transitions, contention


def _chunk_map():
    return {"chunks": [{"chunk_id": "c0", "turn_ids": ["e1"]}]}


def _good_reply(topics, contention):
    return {
        "narrative": "Cache work then login perf.",
        "findings": [
            {"kind": "tension", "title": "user pushed back", "rationale": "r",
             "supporting_refs": [{"layer": "contention",
                                  "annotation_id": contention[0]["annotation_id"]}]},
            {"kind": "case-study-candidate", "title": "two-topic session", "rationale": "r",
             "supporting_refs": [{"layer": "topic",
                                  "annotation_id": topics[0]["annotation_id"]}]},
        ],
    }


def test_response_shape():
    topics, transitions, contention = _records()
    resp = run(SRC, FN, _chunk_map(), topics, transitions, contention,
               router=Stub(_good_reply(topics, contention)))
    assert resp["pass_id"] == PASS_ID
    assert resp["records_sha256"]
    assert resp["tokens_in"] == 50 and resp["tokens_out"] == 20
    assert resp["flow"]["narrative"]


def test_flow_edges_derived_from_transitions():
    _topics, transitions, _cont = _records()
    flow = derive_flow(transitions)
    assert flow["intra_session_edges"] == [
        {"from_topic_id": "t0", "to_topic_id": "t1", "relation": "sequential"}]
    for e in flow["intra_session_edges"]:
        assert e["relation"] in ("revival", "overlap", "nested", "sequential")


def test_relation_type_enum_full_mapping():
    from topic_flow_review import derive_flow
    trs = [{"payload": {"from_topic_id": "t0", "to_topic_id": "t1", "type": t}}
           for t in ("contiguous", "revival", "overlap", "nested")]
    rels = {e["relation"] for e in derive_flow(trs)["intra_session_edges"]}
    assert rels == {"sequential", "revival", "overlap", "nested"}


def test_findings_grounded_and_invalid_refs_dropped():
    topics, transitions, contention = _records()
    bad = Stub({"narrative": "x", "findings": [
        {"kind": "observation", "title": "phantom", "rationale": "r",
         "supporting_refs": [{"layer": "topic", "annotation_id": "nonexistent-000000000"}]},
    ]})
    resp = run(SRC, FN, _chunk_map(), topics, transitions, contention, router=bad)
    assert resp["findings"]  # mechanical fallback keeps findings grounded
    for f in resp["findings"]:
        for ref in f["supporting_refs"]:
            assert ref["annotation_id"] != "nonexistent-000000000"


def test_records_and_flow_persisted():
    topics, transitions, contention = _records()
    resp = run(SRC, FN, _chunk_map(), topics, transitions, contention,
               router=Stub(_good_reply(topics, contention)))
    kinds = {r["kind"] for r in resp["records"]}
    assert kinds == {"finding", "flow"}
    flow_rec = next(r for r in resp["records"] if r["kind"] == "flow")
    assert flow_rec["payload"]["narrative"]


def test_no_router_no_network():
    topics, transitions, contention = _records()
    resp = run(SRC, FN, _chunk_map(), topics, transitions, contention, router=None)
    assert resp["findings"]  # mechanical fallback
    assert resp["tokens_in"] == 0
    import topic_flow_review as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("urllib", "requests", "socket", "open(", "os."):
        assert banned not in src
    assert "invalid_request" in src
