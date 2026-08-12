"""Fleet orchestration tests (end-to-end with stubbed agents):
trigger -> agent run -> artifact -> proposal -> review -> push."""

from __future__ import annotations

import json
import os

from conftest import (SequenceStub, approval_vote, case_study_response,
                      good_proposal, make_graph, make_packet,
                      reflection_response)

from memory_plane.fleet import FleetRunner, extract_json, shape_errors
from memory_plane.store import ArtifactStore
from memory_plane.triggers import TriggerEvaluator


def _runner(tmp_path, client, skills_repo=None, dry_run=False, sync_memory=False):
    db = str(tmp_path / "fleet.db")
    evaluator = TriggerEvaluator(str(tmp_path), ArtifactStore(db),
                                 cadence_mb=0.001, retro_mb=0.001)
    return FleetRunner(str(tmp_path), client, db_path=db,
                       skills_repo=str(skills_repo) if skills_repo else None,
                       dry_run=dry_run, evaluator=evaluator,
                       sync_memory=sync_memory)


def _pad_packet(path: str) -> None:
    """Pad a packet file past the (tiny) MB floors used in tests."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["pad"] = "x" * 4096
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_case_study_run_stores_artifact(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl",
                cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    client = SequenceStub({"case-study-curator": [case_study_response("cs-1")]})
    runner = _runner(tmp_path, client, dry_run=True)
    stats = runner.run_once()
    assert "case-study" in stats["agents_run"]
    art = runner.store.get_artifact("cs-1")
    assert art["type"] == "case-study"
    assert art["agent"] == "agent-case-study-curator"
    assert art["evidence_links"] == [{"type": "packet", "ref": "reasonix-s1",
                                      "note": None}]
    assert art["trigger_reasons"] == stats["triggers"]["case-study"]
    runner.close()


def test_reflection_proposal_reviewed_and_pushed(tmp_path, skills_repo):
    _pad_packet(make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]]))
    client = SequenceStub({
        "reflection": [reflection_response(good_proposal())],
        "case-study-curator": [approval_vote("ok")],
        "retrospective": [approval_vote("ok")],
        "meta-agent": [approval_vote("ok")],
    })
    runner = _runner(tmp_path, client, skills_repo=skills_repo)
    # the cited case study exists in the store (realistic: curator ran first)
    runner.store.add_artifact({"artifact_id": "cs-1", "type": "case-study",
                               "agent": "agent-case-study-curator",
                               "evidence_links": []})
    stats = runner.run_once()
    # agents are called by NAME (the Letta model handle), not registry id
    assert client.calls[0][0] == "reflection"
    assert "reflection" in stats["agents_run"]
    # proposal made it through review + push
    proposal = runner.store.proposals("pushed")[0]
    assert proposal["skill_name"] == "fleet-probe-skill"
    pushed = (skills_repo / "skills" / "fleet-probe-skill" / "SKILL.md")
    assert pushed.exists() and "## Provenance" in pushed.read_text()
    # influenced-skills reverse index updated
    assert runner.store.influenced_skills("fleet-probe-skill")[0]["artifact_id"] == "cs-1"
    # trigger floors advanced
    assert runner.store.get_state("reflection.ts")
    runner.close()


def test_review_hold_blocks_push(tmp_path):
    _pad_packet(make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]]))
    client = SequenceStub({
        "reflection": [reflection_response(good_proposal())],
        "case-study-curator": [approval_vote("ok")],
        "retrospective": [json.dumps({"vote": "reject",
                                      "reason": "over-fits one session"})],
        "meta-agent": [approval_vote("ok")],
    })
    runner = _runner(tmp_path, client, skills_repo=None, dry_run=False)
    stats = runner.run_once()
    assert stats["proposals"][0]["status"] == "held"
    assert runner.store.proposals("held")[0]["id"]
    runner.close()


def test_malformed_agent_output_retries_then_records(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl",
                cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    client = SequenceStub({
        "case-study-curator": [
            "not json at all",
            case_study_response("cs-9"),
        ],
    })
    runner = _runner(tmp_path, client, dry_run=True)
    stats = runner.run_once()
    art = runner.store.get_artifact("cs-9")
    assert art is not None
    assert stats["agents_run"]["case-study"]["retried"] is True
    runner.close()


def test_shape_validation_retry_with_feedback(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl",
                cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    bad = json.dumps({"artifact_id": "cs-2", "type": "case-study"})  # missing fields
    client = SequenceStub({
        "case-study-curator": [bad, case_study_response("cs-2")],
    })
    runner = _runner(tmp_path, client, dry_run=True)
    runner.run_once()
    art = runner.store.get_artifact("cs-2")
    assert art is not None and art["retried"] is True
    # the retry prompt carried the validation errors
    second = client.calls[1][1]
    assert "failed validation" in second
    runner.close()


def test_invalid_proposal_is_recorded_not_pushed(tmp_path, skills_repo):
    _pad_packet(make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]]))
    p = good_proposal()
    p["motivated_by"] = []  # ungrounded -> invalid
    client = SequenceStub({"reflection": [reflection_response(p)]})
    runner = _runner(tmp_path, client, skills_repo=skills_repo)
    stats = runner.run_once()
    assert stats["proposals"][0]["status"] == "invalid"
    assert not (skills_repo / "skills" / "fleet-probe-skill").exists()
    runner.close()


def test_no_triggers_no_runs(tmp_path):
    _pad_packet(make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]]))
    db = ArtifactStore(str(tmp_path / "fleet.db"))
    db.set_state("reflection.ts", str(os.path.getmtime(__file__)))  # future-ish
    client = SequenceStub({})
    runner = FleetRunner(str(tmp_path), client, db_path=str(tmp_path / "fleet.db"),
                         evaluator=TriggerEvaluator(str(tmp_path), db))
    stats = runner.run_once()
    assert stats["agents_run"] == {}
    runner.close()


def test_extract_json_tolerant():
    raw = 'Sure!\n{"a": {"b": [1, 2]}}\nDone.'
    assert extract_json(raw) == {"a": {"b": [1, 2]}}
    try:
        extract_json("no json")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_shape_errors():
    agent = {"output_schema": {"required": ["artifact_id", "type"],
                               "type": "case-study"}}
    assert shape_errors({"artifact_id": "x", "type": "case-study"}, agent) == []
    assert any("artifact_id" in e for e in
               shape_errors({"type": "case-study"}, agent))
    assert any("type" in e for e in shape_errors({"artifact_id": "x",
                                                  "type": "reflection"}, agent))
    assert any("artifact_id" in e for e in
               shape_errors({"artifact_id": "../evil", "type": "case-study"},
                            agent))


def test_evaluator_crash_does_not_kill_run_once(tmp_path):
    """A corrupt trigger state must produce a recorded error, not a crash."""
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]])

    class BoomEvaluator:
        def evaluate(self):
            raise RuntimeError("corrupt state")

    runner = FleetRunner(str(tmp_path), SequenceStub({}),
                         db_path=str(tmp_path / "fleet.db"),
                         evaluator=BoomEvaluator())
    stats = runner.run_once()
    assert stats["agents_run"] == {}
    assert any("trigger evaluation failed" in e for e in stats["errors"])
    runner.close()


def _meta_artifact() -> str:
    return json.dumps({
        "artifact_id": "meta-1", "type": "system-memory",
        "period": {"from": "2026-08-01", "to": "2026-08-12"},
        "memories": [{"key": "free first", "value": "Use free models first."}],
        "process_updates": [{"proposal_id": "ps-1", "kind": "process-skill",
                             "description": "Review checklist",
                             "reasoning": "votes inconsistent",
                             "body": "Verify grounding before voting."}],
        "evidence_links": [{"type": "reflection", "ref": "ref-1"}]})


def test_meta_memory_sync_writes_memfs_files(tmp_path):
    """--sync-memory: the meta-agent's memories + process skills land in its
    MemFS files (dry-run here; the live path mirrors bootstrap's transport)."""
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]])
    client = SequenceStub({"meta-agent": [_meta_artifact()]})
    runner = _runner(tmp_path, client, dry_run=True, sync_memory=True)
    # meta fires on proposal accumulation (or first cycle with proposals)
    runner.store.add_artifact({"artifact_id": "ref-1", "type": "reflection",
                               "agent": "agent-reflection", "evidence_links": []})
    runner.store.add_proposal({"proposal_id": "p1", "kind": "no-proposal",
                               "judgement": "naturally-hard",
                               "reasoning": "inherently hard",
                               "motivated_by": []}, "ref-1")
    stats = runner.run_once()
    assert "meta" in stats["agents_run"]
    assert stats["memory_sync"] == ["memories.md (dry-run)",
                                    "process-skills.md (dry-run)"]
    # artifact stored with its provenance
    art = runner.store.get_artifact("meta-1")
    assert art["type"] == "system-memory"
    runner.close()


def test_meta_memory_sync_failure_recorded_not_fatal(tmp_path):
    """A failing memory sync is recorded, and the loop keeps going."""
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]])
    client = SequenceStub({"meta-agent": [_meta_artifact()]})
    runner = _runner(tmp_path, client, dry_run=True, sync_memory=True)
    runner.store.add_artifact({"artifact_id": "ref-1", "type": "reflection",
                               "agent": "agent-reflection", "evidence_links": []})
    runner.store.add_proposal({"proposal_id": "p1", "kind": "no-proposal",
                               "judgement": "naturally-hard",
                               "reasoning": "inherently hard",
                               "motivated_by": []}, "ref-1")
    runner.memfs_host = "no-such-host.invalid"
    runner.dry_run = False  # force the real (failing) ssh path
    stats = runner.run_once()
    assert any("memory sync failed" in e for e in stats["errors"])
    runner.close()
