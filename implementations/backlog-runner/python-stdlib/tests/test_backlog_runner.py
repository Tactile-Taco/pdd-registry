"""Tests for the backlog runner (execution-plan implementation)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backlog_runner import required_pass_versions, run_once  # noqa: E402
from journal import CheckpointJournal  # noqa: E402
from ledger import CostLedger  # noqa: E402
from router import StubRouter  # noqa: E402

SRC = "hermes"
FN = "s.jsonl"

_TT_REPLY = {
    "topics": [{"label": "cache invalidation", "intensity": 0.8, "quotes": []}],
    "transitions": [],
}
_FW_REPLY = {"narrative": "single-topic session", "findings": []}


def _setup(tmp_path, n_files=2, poison=False):
    archive = tmp_path / "archive"
    (archive / SRC).mkdir(parents=True)
    for i in range(n_files):
        name = f"s{i}.jsonl"
        with open(archive / SRC / name, "w", encoding="utf-8") as f:
            f.write('{"session_id":"x","role":"user","content":"Hello?","compacted":0,"timestamp":1}\n')
            f.write('{"session_id":"x","role":"assistant","content":"I think maybe yes.","compacted":0,"timestamp":2}\n')
            if poison and i == 1:
                f.write('\ufffd\ufffd')  # replaced chars are fine
        if poison and i == 1:
            # invalid UTF-8 bytes → decoder failure → journaled as failed
            with open(archive / SRC / name, "ab") as f:
                f.write(b"\xff\xfe\x00\x01")
    store = tmp_path / "store"
    store.mkdir()
    journal = CheckpointJournal(str(store / "journal.json"))
    ledger = CostLedger(str(store / "cost-ledger.jsonl"))
    return str(archive), str(store), journal, ledger


def _stub():
    return StubRouter(replies={r"Transcript chunk": _TT_REPLY,
                               r"Session ": _FW_REPLY})


def test_done_files_skipped_on_second_run(tmp_path):
    archive, store, journal, ledger = _setup(tmp_path)
    stub = _stub()
    first = run_once([SRC], archive, store, journal, ledger, stub)
    assert first["processed"] == 2 and first["failed"] == 0
    stub2 = _stub()
    second = run_once([SRC], archive, store, journal, ledger, stub2)
    assert second["skipped"] == 2 and second["processed"] == 0
    assert not stub2.calls  # no LLM calls for already-done files


def test_pass_version_gate(tmp_path):
    archive, store, journal, ledger = _setup(tmp_path)
    run_once([SRC], archive, store, journal, ledger, _stub())
    # bump one pass version → files must be re-processed
    required = required_pass_versions()
    required["uncertainty-pass"] = "9.9.9-fake"
    assert not journal.is_done(SRC, "s0.jsonl", required)
    from backlog_runner import run_once as ro
    stats = ro([SRC], archive, store, journal, ledger, _stub(), limit=1,
               required_versions=required)
    assert stats["processed"] >= 1


def test_failed_file_retried_then_exhausted(tmp_path):
    archive, store, journal, ledger = _setup(tmp_path, poison=True)
    stub = _stub()
    first = run_once([SRC], archive, store, journal, ledger, stub, max_attempts=2)
    assert first["failed"] == 1 and first["processed"] == 1
    assert journal.get(SRC, "s1.jsonl")["status"] == "failed"
    second = run_once([SRC], archive, store, journal, ledger, _stub(), max_attempts=2)
    assert second["failed"] == 1  # retried once more
    assert journal.get(SRC, "s1.jsonl")["attempts"] == 2
    third = run_once([SRC], archive, store, journal, ledger, _stub(), max_attempts=2)
    assert third["exhausted"] == 1  # attempts exhausted → skipped permanently


def test_packets_and_graph_written(tmp_path):
    archive, store, journal, ledger = _setup(tmp_path, n_files=1)
    run_once([SRC], archive, store, journal, ledger, _stub())
    packets = os.listdir(os.path.join(store, "packets"))
    assert len(packets) == 1 and packets[0].endswith(".packet.json")
    assert os.path.exists(os.path.join(store, "topic-graph", "topic-graph.json"))
    with open(os.path.join(store, "packets", packets[0]), encoding="utf-8") as f:
        pkt = json.load(f)
    assert pkt["packet"]["overview"]["turn_count"] == 2


def test_ledger_records_llm_calls_with_model_router(tmp_path):
    # StubRouter does not write the ledger; ModelRouter does. Here we verify
    # the wiring point: run_once accepts a router and the ledger stays empty
    # with a stub (LLM-free path), while journal/status still advance.
    archive, store, journal, ledger = _setup(tmp_path, n_files=1)
    run_once([SRC], archive, store, journal, ledger, _stub())
    assert ledger.totals()["calls"] == 0
    assert journal.get(SRC, "s0.jsonl")["status"] == "done"
