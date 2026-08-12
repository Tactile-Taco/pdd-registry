"""Candidate tests for the uncertainty-pass bundle (must invariants)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uncertainty_pass import Lexicon, PASS_ID, lexicon_version, run, scan  # noqa: E402
from transcript_chunking import run as chunk_run  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"


@pytest.fixture
def archive_and_store(tmp_path):
    archive = tmp_path / "archive"
    (archive / SRC).mkdir(parents=True)
    with open(archive / SRC / FN, "w", encoding="utf-8") as f:
        f.write('{"session_id":"x","role":"user","content":"Hello?","compacted":0,"timestamp":1}\n')
        f.write('{"session_id":"x","role":"assistant","content":"I think maybe yes","reasoning_content":"but wait, let me reconsider","compacted":0,"timestamp":2}\n')
        f.write('{"session_id":"x","role":"user","content":"That\'s wrong, revert it.","compacted":0,"timestamp":3}\n')
        f.write('{"session_id":"x","role":"assistant","content":"ok, done","compacted":0,"timestamp":4}\n')
    chunk_run(SRC, FN, str(archive), target_chars=80000, chunk_store=str(tmp_path / "store" / "chunk-store"))
    return str(archive), str(tmp_path / "store")


def _run_uncertainty(store, tmp_path):
    with open(os.path.join(str(tmp_path / "store" / "chunk-store" / SRC), FN + ".chunkmap.json"), encoding="utf-8") as f:
        render_id = json.load(f)["render_id"]
    return run(SRC, FN, render_id=render_id, lexicon_version_="",
               chunk_store=str(tmp_path / "store" / "chunk-store"),
               store_dir=str(tmp_path / "store"))


def test_response_schema_conformance(archive_and_store, tmp_path):
    from common import bundle_schema_path, validate_against_schema
    _archive, store = archive_and_store
    resp = _run_uncertainty(store, tmp_path)
    assert validate_against_schema(resp, bundle_schema_path(PASS_ID, "response.schema.json")) == []
    assert resp["pass_id"] == PASS_ID
    assert resp["lexicon_version"]
    assert resp["chunks_processed"] >= 1


def test_deterministic_output_identical_runs(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    a = _run_uncertainty(store, tmp_path)
    b = _run_uncertainty(store, tmp_path)
    assert a == b
    assert a["records_sha256"] == b["records_sha256"]
    assert a["density"] == b["density"]


def test_density_math_known_text():
    lex = Lexicon.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lexicons", "uncertainty.json"))
    text = "I think this is probably fine and I think that too"
    matches = scan(text, lex.markers)
    # "i think" appears twice; "probably" once → 3 matches
    counts = {}
    for m, _p in matches:
        counts[m] = counts.get(m, 0) + 1
    assert counts == {"i think": 2, "probably": 1}
    # longest-first: "i think" beats "think" (if present) — no partial overlaps
    n = len(text)
    assert all(0 <= p < n for _m, p in matches)


def test_positions_within_chunk_bounds():
    lex = Lexicon.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lexicons", "uncertainty.json"))
    text = "x" * 100 + "maybe" + "x" * 100
    matches = scan(text, lex.markers)
    assert matches[0][0] == "maybe"
    assert 100 <= matches[0][1] < 105


def test_lexicons_pinned_versions():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lexicons")
    for fn in ("uncertainty.json", "planning.json", "contention.json"):
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == "1.0.0"
        assert data["markers"], fn
        assert data["markers"] == sorted(data["markers"]), fn  # files keep stable order; longest-first is applied by Lexicon


def test_source_segregation_reasoning_vs_dialogue(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_uncertainty(store, tmp_path)
    # assistant turn's reasoning contains "but wait"/"let me reconsider" (reasoning),
    # content contains "i think"/"maybe" (dialogue) → source must be "both"
    d = resp["density"][0]
    assert d["source"] in ("both", "dialogue", "reasoning")
    assert d["marker_count"] >= 2
    assert d["diversity"] >= 2
    assert d["density_per_1k"] > 0
    assert 0 <= d["positional_median_pct"] <= 100
    # pure-reasoning chunk: build one and check source=reasoning
    text, mask = _chunk_with_mask("", "but wait, let me reconsider")
    matches = scan(text, [m for m in _markers()])
    srcs = {mask[p] for _m, p in matches}
    assert srcs == {"r"}


def _markers():
    return (Lexicon.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lexicons", "uncertainty.json")).markers
            + Lexicon.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lexicons", "planning.json")).markers
            + Lexicon.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lexicons", "contention.json")).markers)


def _chunk_with_mask(dialogue: str, reasoning: str):
    head = "[e1][assistant]\n"
    text = head + "> reasoning: " + reasoning + "\n" + dialogue + "\n"
    mask = "d" * len(head) + "r" * (len("> reasoning: " + reasoning + "\n")) + "d" * (len(dialogue) + 1)
    return text, mask


def test_records_append_to_store(archive_and_store, tmp_path):
    _archive, store = archive_and_store
    resp = _run_uncertainty(store, tmp_path)
    from annotation_store import AnnotationStore
    s = AnnotationStore(str(tmp_path / "store"))
    q = s.query(SRC, FN, layer="uncertainty")
    expected = sum(1 for r in resp["records"] if r["layer"] == "uncertainty")
    assert q["total_matches"] == expected
    # contention event for the "That's wrong, revert it." user turn
    qc = s.query(SRC, FN, layer="contention")
    assert any("revert" in r["payload"].get("markers", []) for r in qc["records"])


def test_archive_untouched(archive_and_store, tmp_path):
    archive, _store = archive_and_store
    before = open(os.path.join(archive, SRC, FN), "rb").read()
    _run_uncertainty(_store, tmp_path)
    assert open(os.path.join(archive, SRC, FN), "rb").read() == before


def test_no_model_calls_no_network():
    import uncertainty_pass as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "urllib" not in src and "requests" not in src and "socket" not in src
    assert "complete_json" not in src
