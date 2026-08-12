"""Candidate tests for uncertainty-pass (pure core; in-memory fixtures)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uncertainty_pass import (  # noqa: E402
    ALL_MARKERS, CONTENTION_MARKERS, PASS_ID, PLANNING_MARKERS,
    UNCERTAINTY_MARKERS, scan, run,
)

SRC = "hermes"
FN = "s.jsonl"


def _fixture():
    turns = [
        {"event_id": "e1", "role": "user", "content": "Hello?", "reasoning": ""},
        {"event_id": "e2", "role": "assistant", "content": "I think maybe yes",
         "reasoning": "but wait, let me reconsider"},
        {"event_id": "e3", "role": "user", "content": "That's wrong, revert it.",
         "reasoning": ""},
        {"event_id": "e4", "role": "assistant", "content": "ok, done", "reasoning": ""},
    ]
    chunk_map = {"chunks": [{"chunk_id": "c0",
                             "turn_ids": ["e1", "e2", "e3", "e4"]}]}
    return chunk_map, turns


def test_response_shape_and_schema_fields():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns)
    assert resp["pass_id"] == PASS_ID
    assert resp["lexicon_version"] == "uncertainty@1.0.0+planning@1.0.0+contention@1.0.0"
    assert resp["chunks_processed"] == 1
    assert resp["records_sha256"]
    d = resp["density"][0]
    assert set(d) == {"chunk_id", "char_count", "marker_count", "density_per_1k",
                      "markers", "diversity", "positional_median_pct", "variance", "source"}
    assert d["char_count"] >= 1
    assert d["density_per_1k"] > 0
    assert 0 <= d["positional_median_pct"] <= 100


def test_deterministic_output_identical_runs():
    chunk_map, turns = _fixture()
    a = run(SRC, FN, chunk_map, turns)
    b = run(SRC, FN, chunk_map, turns)
    assert a == b
    assert a["records_sha256"] == b["records_sha256"]


def test_density_math_known_text():
    text = "I think this is probably fine and I think that too"
    counts = {}
    for m, _p in scan(text):
        counts[m] = counts.get(m, 0) + 1
    assert counts == {"i think": 2, "probably": 1}
    # longest-first: no partial overlaps; positions in bounds
    assert all(0 <= p < len(text) for _m, p in scan(text))


def test_source_segregation_reasoning_vs_dialogue():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns)
    d = resp["density"][0]
    # matches appear in both the reasoning line and dialogue content
    assert d["source"] == "both"
    assert d["diversity"] >= 2
    # pure-reasoning: only reasoning content with markers
    chunk_map2 = {"chunks": [{"chunk_id": "c0", "turn_ids": ["e2"]}]}
    turns2 = [{"event_id": "e2", "role": "assistant", "content": "ok",
               "reasoning": "but wait, let me reconsider"}]
    d2 = run(SRC, FN, chunk_map2, turns2)["density"][0]
    assert d2["source"] == "reasoning"


def test_contention_events_on_user_turns():
    chunk_map, turns = _fixture()
    resp = run(SRC, FN, chunk_map, turns)
    ct = [r for r in resp["records"] if r["kind"] == "contention-event"]
    assert len(ct) == 1
    assert ct[0]["target"]["event_id"] == "e3"
    assert "revert" in ct[0]["payload"]["markers"]


def test_lexicons_pinned_and_stable():
    assert len(UNCERTAINTY_MARKERS) >= 30
    assert len(PLANNING_MARKERS) >= 20
    assert len(CONTENTION_MARKERS) >= 15
    # longest-first ordering guarantee used by scan
    for m in ALL_MARKERS:
        assert m == sorted(ALL_MARKERS, key=lambda x: (-len(x), x))[ALL_MARKERS.index(m)]


def test_no_model_calls_no_network():
    import uncertainty_pass as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("urllib", "requests", "socket", "open(", "os.", "complete_json"):
        assert banned not in src
    assert "invalid_request" in src


def test_invalid_source_rejected():
    with pytest.raises(ValueError):
        run("nope", FN, {"chunks": []}, [])
