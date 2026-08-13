"""Backlog survey-mode digestion tests (selection, run, resumability)."""

from __future__ import annotations

import json

from conftest import (case_study_response, make_graph, make_packet,
                      reflection_response)

from memory_plane.client import StubClient
from memory_plane.triggers import load_packets
from backlog_digestion import BacklogSurvey, summarize_packet

RETRO = json.dumps({
    "artifact_id": "ret-1", "type": "retrospective",
    "checkpoint": {"kind": "concluded-cluster", "desc": "x"},
    "period": {"from": "2026-08-01", "to": "2026-08-12"},
    "session_refs": ["reasonix-s1"], "summary": "s",
    "aggregated_patterns": [], "skill_proposals": [], "evidence_links": [],
})

META = json.dumps({
    "artifact_id": "meta-1", "type": "system-memory",
    "period": {"from": "2026-08-01", "to": "2026-08-12"},
    "memories": [{"key": "pacing", "value": "pace calls"}],
    "process_updates": [], "evidence_links": [],
})


def _survey(tmp_path, script, **kw):
    client = StubClient({k: v[0] for k, v in script.items()})
    db = str(tmp_path / "survey.db")
    opts = dict(skills_repo=None, dry_run=True, pace=0.0, top_k=5, batch_mb=0.0001)
    opts.update(kw)
    return BacklogSurvey(str(tmp_path), client, db_path=db, **opts), client


def test_case_study_ranking_puts_hot_first(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    make_packet(tmp_path, "reasonix", "s2.jsonl", cells=[[0.1, 0.1, 0.1, 0.1, 0.1]])
    runner, _ = _survey(tmp_path, {})
    jobs = runner._case_study_jobs(load_packets(str(tmp_path)))
    assert jobs[0][2] == "reasonix-s1"   # hot session ranked first
    assert jobs[1][2] == "reasonix-s2"


def test_survey_jobs_shape(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    make_packet(tmp_path, "reasonix", "s2.jsonl", cells=[[0.1, 0.1, 0.1, 0.1, 0.1]])
    make_graph(tmp_path, sessions=["reasonix-s1", "reasonix-s2"],
               edges=[("reasonix-s1::t1", "reasonix-s2::t1", "similar")])
    runner, _ = _survey(tmp_path, {})
    jobs = runner.survey_jobs()
    kinds = [j[3] for j in jobs]
    assert "case-study" in kinds and "reflection" in kinds
    assert "system-memory" in kinds          # meta once
    # every job is a (key, agent_name, task, type) tuple
    assert all(len(j) == 4 for j in jobs)


def test_run_survey_stores_artifacts_and_is_resumable(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    make_packet(tmp_path, "reasonix", "s2.jsonl", cells=[[0.1, 0.1, 0.1, 0.1, 0.1]])
    script = {
        "case-study-curator": [case_study_response("cs-1")],
        "reflection": [reflection_response()],
        "retrospective": [RETRO],
        "meta-agent": [META],
    }
    runner, client = _survey(tmp_path, script, do_retrospective=False)
    stats = runner.run_survey()
    assert stats["done"] >= 2                     # case studies + reflection
    assert stats["errors"] == []
    assert runner.store.get_artifact("cs-1")["type"] == "case-study"
    # all completed jobs are recorded -> a second run skips them
    stats2 = runner.run_survey()
    assert stats2["skipped"] == stats2["jobs_total"]
    assert stats2["done"] == 0


def test_meta_run_records_state_without_memfs(tmp_path):
    # meta run with sync_memory=False must not require an M6 connection
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1, 0.1]])
    runner, _ = _survey(tmp_path, {"meta-agent": [META]},
                        do_case_study=False, do_reflection=False,
                        do_retrospective=False)
    stats = runner.run_survey()
    assert stats["done"] == 1
    assert runner.store.get_artifact("meta-1")["type"] == "system-memory"


def test_summarize_packet_compact(tmp_path):
    p = make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[2.0]])
    with open(p) as f:
        data = json.load(f)
    s = summarize_packet(data, verbose=False)
    assert "s1.jsonl" in s and "hot_cells=1" in s
