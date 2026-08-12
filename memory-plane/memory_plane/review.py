"""Peer review for skill-improvement proposals.

Design: proposals are reviewed by the OTHER fleet agents; on unanimous
approval the proposal is auto-pushed to the canonical skills repo. Any reject
holds the proposal with the reasons recorded. Git is the audit trail.

Votes are model calls (stochastic); the tally itself is deterministic and
fail-closed: an unparseable vote counts as a reject.
"""

from __future__ import annotations

import json

from .agent_defs import agent_def, agent_ids

REVIEW_TEMPLATE = """\
Peer review of a skill-improvement proposal from the {author} agent.

Proposal:
{proposal}

Review it against your own experience of the skills involved. Respond with
exactly one JSON object:
{{"vote": "approve" | "reject", "reason": "<why, one or two sentences>"}}
Reject if the proposal is ungrounded (no motivating artifacts), over-fits a
single session, would harm other harnesses, or the skill would be worse with
the change. Otherwise approve.
"""


def tally_votes(votes: list[dict]) -> dict:
    """Deterministic tally: unanimous approve -> approved, else held."""
    if not votes:
        return {"verdict": "held", "reasons": ["no votes collected"]}
    reasons = [v["reason"] for v in votes if v.get("vote") == "reject"]
    if any(v.get("vote") == "reject" for v in votes):
        return {"verdict": "held", "reasons": reasons}
    return {"verdict": "approved", "reasons": []}


def parse_vote(raw: str) -> dict:
    """Tolerant JSON extraction; fail-closed -> reject."""
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object")
        obj = json.loads(raw[start:end + 1])
        vote = obj.get("vote")
        if vote not in ("approve", "reject"):
            raise ValueError(f"bad vote value: {vote!r}")
        return {"vote": vote, "reason": str(obj.get("reason", ""))[:500]}
    except (ValueError, json.JSONDecodeError) as e:
        return {"vote": "reject", "reason": f"unparseable vote: {e}"}


def run_review(proposal: dict, client, store, voters: list[str] | None = None,
               author: str = "fleet") -> dict:
    """Ask each voter agent for a vote, record them, and tally.

    Voters are identified by registry id; the client is called with the
    agent's NAME (the model handle the Letta server exposes)."""
    voters = voters or [a for a in agent_ids() if a != author]
    prompt = REVIEW_TEMPLATE.format(
        author=author, proposal=json.dumps(proposal, ensure_ascii=False, indent=1))
    for voter in voters:
        raw = client.chat(agent_def(voter)["name"], prompt)
        vote = parse_vote(raw)
        store.add_vote(proposal["proposal_id"], voter, vote["vote"], vote["reason"])
    result = tally_votes(store.votes(proposal["proposal_id"]))
    store.set_proposal_status(proposal["proposal_id"],
                              "approved" if result["verdict"] == "approved" else "held")
    return result
