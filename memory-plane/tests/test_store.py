"""Artifact store tests: provenance edges, influenced-skills reverse index,
proposals + votes, run state."""

from __future__ import annotations

from conftest import case_study_response

from memory_plane.proposals import extract_proposals
from memory_plane.store import ArtifactStore


def test_artifact_round_trip_with_evidence_edges(store):
    art = {
        "artifact_id": "cs-1", "type": "case-study", "agent": "agent-case-study-curator",
        "model": "m", "evidence_links": [{"type": "packet", "ref": "reasonix-s1"}],
    }
    store.add_artifact(art)
    got = store.get_artifact("cs-1")
    assert got["type"] == "case-study"
    assert got["evidence_links"] == [{"type": "packet", "ref": "reasonix-s1",
                                      "note": None}]
    assert store.count("case-study") == 1
    assert store.count() == 1


def test_influenced_skills_reverse_index(store):
    store.add_artifact({"artifact_id": "cs-1", "type": "case-study",
                        "agent": "a", "evidence_links": []})
    store.link_skill("cs-1", "web-perf", "## Provenance", "found a regression")
    links = store.influenced_skills("web-perf")
    assert links == [{"artifact_id": "cs-1", "section": "## Provenance",
                      "impact": "found a regression"}]
    assert store.influenced_skills("nope") == []


def test_proposals_and_votes(store):
    store.add_proposal({"proposal_id": "p1", "kind": "new-skill",
                        "skill_name": "x", "judgement": "concrete-fix",
                        "reasoning": "r", "motivated_by": [{"artifact_id": "cs-1",
                                                            "impact": "i"}]},
                       "ref-1")
    assert store.proposals("proposed")[0]["id"] == "p1"
    store.add_vote("p1", "agent-reflection", "approve", "good")
    store.add_vote("p1", "agent-retrospective", "reject", "no")
    store.set_proposal_status("p1", "held")
    assert store.proposals("held")[0]["id"] == "p1"
    votes = store.votes("p1")
    assert {v["voter"] for v in votes} == {"agent-reflection", "agent-retrospective"}


def test_state_round_trip(store):
    assert store.get_state("nope") is None
    store.set_state("reflection.ts", "123")
    assert store.get_state("reflection.ts") == "123"


def test_extract_proposals_assigns_ids():
    art = {"artifact_id": "ref-1",
           "skill_proposals": [{"kind": "no-proposal", "reasoning": "hard"},
                               {"kind": "new-skill", "skill_name": "x",
                                "motivated_by": [], "reasoning": "r"}]}
    ps = extract_proposals(art)
    assert [p["proposal_id"] for p in ps] == ["ref-1-p1", "ref-1-p2"]
    assert ps[0]["artifact_id"] == "ref-1"


def test_memory_store_isolation():
    a = ArtifactStore(":memory:")
    b = ArtifactStore(":memory:")
    a.set_state("k", "v")
    assert b.get_state("k") is None
    a.close()
    b.close()
