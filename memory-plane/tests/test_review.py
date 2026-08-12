"""Peer-review tests: deterministic tally, fail-closed parsing, and the full
review flow with a stubbed client."""

from __future__ import annotations

import json

from conftest import approval_vote, reject_vote

from memory_plane.review import parse_vote, run_review, tally_votes


def test_tally_unanimous_approval():
    r = tally_votes([{"vote": "approve", "reason": "a"},
                     {"vote": "approve", "reason": "b"}])
    assert r["verdict"] == "approved"


def test_tally_any_reject_holds():
    r = tally_votes([{"vote": "approve", "reason": "a"},
                     {"vote": "reject", "reason": "over-fits"}])
    assert r["verdict"] == "held"
    assert r["reasons"] == ["over-fits"]


def test_tally_empty_holds():
    assert tally_votes([])["verdict"] == "held"


def test_parse_vote_tolerates_wrapped_text():
    raw = "Here is my review:\n" + approval_vote("grounded") + "\nRegards."
    assert parse_vote(raw)["vote"] == "approve"


def test_parse_vote_fail_closed():
    assert parse_vote("I think this is bad")["vote"] == "reject"
    assert parse_vote('{"vote": "maybe"}')["vote"] == "reject"
    assert parse_vote("")["vote"] == "reject"


def test_run_review_approves_with_unanimous_votes(store):
    class V:
        def chat(self, agent_id, task):
            return approval_vote("looks grounded")

    proposal = {"proposal_id": "p1", "kind": "new-skill", "skill_name": "x",
                "reasoning": "grounded in cs-1"}
    store.add_proposal(proposal, "ref-1")
    result = run_review(proposal, V(), store, author="agent-reflection")
    assert result["verdict"] == "approved"
    assert store.proposals("approved")[0]["id"] == "p1"
    votes = store.votes("p1")
    assert {v["voter"] for v in votes} == {"agent-case-study-curator",
                                           "agent-retrospective", "agent-meta"}


def test_run_review_holds_on_any_reject(store):
    class V:
        def chat(self, agent_id, task):
            return reject_vote("unproven") if agent_id == "agent-retrospective" \
                else approval_vote("ok")

    proposal = {"proposal_id": "p2", "kind": "edit-skill", "skill_name": "y",
                "reasoning": "grounded in cs-2"}
    store.add_proposal(proposal, "ref-1")
    result = run_review(proposal, V(), store, author="agent-reflection")
    assert result["verdict"] == "held"
    assert store.proposals("held")[0]["id"] == "p2"


def test_run_review_unparseable_counts_as_reject(store):
    class V:
        def chat(self, agent_id, task):
            return "gibberish"

    proposal = {"proposal_id": "p3", "kind": "new-skill", "skill_name": "z"}
    result = run_review(proposal, V(), store, author="agent-reflection")
    assert result["verdict"] == "held"
    assert store.votes("p3")[0]["vote"] == "reject"
    assert "unparseable" in store.votes("p3")[0]["reason"]
