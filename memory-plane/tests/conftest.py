"""Shared fixtures for the memory-plane tests: synthetic store data, stub
clients, and a tmp git skills repo with a bare remote."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_plane.store import ArtifactStore  # noqa: E402


def make_packet(store_dir: str, source: str, filename: str,
                cells: list[list[float | None]], fidelity: str = "full",
                tension: list[str] | None = None,
                narrative: str = "a session", turn_count: int = 100) -> str:
    """Write a valid-shaped reflection packet and return its path."""
    packet = {
        "packet_id": f"{source}-{filename}-1",
        "packet": {
            "session": {"source": source, "filename": filename,
                        "render_id": "r1", "fidelity_class": fidelity},
            "provenance": {"passes": [{"pass_id": "p1", "pass_version": "1",
                                       "layer": "uncertainty"}],
                           "baselines_ref": "baselines.json"},
            "overview": {"turn_count": turn_count,
                         "chunk_count": len(cells[0]) if cells else 0,
                         "fidelity_note": ""},
            "tension_summary": tension or [],
            "topic_flow": {"narrative": narrative},
            "case_study_candidates": [],
            "baseline_refs": [],
            "stats": {},
            "heatmap": {"matrix": {
                "rows": ["uncertainty"],
                "columns": [f"c{i}" for i in range(len(cells[0]) if cells else 0)],
                "cells": cells,
                "normalization": "baseline-deviation"},
                "render": ""},
        },
        "packet_sha256": "0" * 64,
    }
    os.makedirs(os.path.join(store_dir, "packets"), exist_ok=True)
    path = os.path.join(store_dir, "packets", f"{source}-{filename}.packet.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(packet, f)
    return path


def make_graph(store_dir: str, edges: list[tuple[str, str, str]] | None = None,
               sessions: list[str] | None = None) -> None:
    nodes = {f"{s}::t1": {"label": "topic", "intensity": 0.5} for s in (sessions or [])}
    graph = {"nodes": nodes,
             "edges": [{"from_node_id": a, "to_node_id": b, "type": t,
                        "similarity": 0.8} for a, b, t in (edges or [])],
             "sessions": sessions or [], "index_size": 0,
             "index_sha256": "0" * 64}
    d = os.path.join(store_dir, "topic-graph")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "topic-graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph, f)


class SequenceStub:
    """Stub client returning responses in order per agent; last one repeats."""

    def __init__(self, script: dict[str, list[str]]) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []

    def chat(self, agent_id: str, task: str, system: str | None = None) -> str:
        self.calls.append((agent_id, task))
        responses = self.script.get(agent_id) or ["{}"]
        return responses[min(len(self.calls) - 1, len(responses) - 1)] \
            if responses else "{}"


def approval_vote(reason: str = "grounded and useful") -> str:
    return json.dumps({"vote": "approve", "reason": reason})


def reject_vote(reason: str = "over-fits one session") -> str:
    return json.dumps({"vote": "reject", "reason": reason})


def case_study_response(artifact_id: str = "cs-1") -> str:
    return json.dumps({
        "artifact_id": artifact_id, "type": "case-study",
        "session": {"source": "reasonix", "filename": "s1.jsonl"},
        "goal": "ship the feature", "progress": "halfway",
        "friction": "tooling was slow",
        "evidence_links": [{"type": "packet", "ref": "reasonix-s1"}],
        "patterns": ["tooling friction"]})


def reflection_response(proposal: dict | None = None, artifact_id: str = "ref-1",
                        extra: dict | None = None) -> str:
    artifact = {
        "artifact_id": artifact_id, "type": "reflection",
        "period": {"from": "2026-08-01", "to": "2026-08-12"},
        "session_refs": ["reasonix-s1"], "summary": "things happened",
        "insights": ["i1"], "patterns": ["p1"],
        "skill_proposals": [proposal] if proposal else [],
        "evidence_links": [{"type": "packet", "ref": "reasonix-s1"}]}
    artifact.update(extra or {})
    return json.dumps(artifact)


def good_proposal() -> dict:
    return {
        "kind": "new-skill", "skill_name": "fleet-probe-skill",
        "title": "Add a probe skill",
        "description": "Probe patterns before committing to a design.",
        "body": "---\nname: fleet-probe-skill\ndescription: Probe first.\n---\n\n"
                "Probe before you commit.\n\n## Provenance\n- cs-1 — tooling friction",
        "judgement": "concrete-fix",
        "motivated_by": [{"artifact_id": "cs-1", "impact": "tooling friction"}],
        "reasoning": "The friction recurred across sessions."}


@pytest.fixture
def store(tmp_path):
    s = ArtifactStore(str(tmp_path / "fleet.db"))
    yield s
    s.close()


@pytest.fixture
def skills_repo(tmp_path):
    """A git repo shaped like the canonical skills repo, with a bare remote."""
    repo = tmp_path / "skills"
    repo.mkdir()
    (repo / "skills").mkdir()
    remote = tmp_path / "skills-remote.git"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    (repo / "skills" / "existing-skill").mkdir()
    (repo / "skills" / "existing-skill" / "SKILL.md").write_text(
        "---\nname: existing-skill\ndescription: Old skill.\n---\n\nOld body.\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                   check=True)
    return repo
