"""Artifact store for the memory plane (sqlite, stdlib).

Holds fleet artifacts (case studies, reflections, retrospectives, system
memories) with provenance edges to transcripts/annotations/packets, the
influenced-skills reverse index, skill-improvement proposals with peer-review
votes, and small run-state key/values (trigger floors).

Provenance model (see docs/memory-plane-design.md):
  artifact --evidence_links--> transcripts+annotations+packets
  artifact --skill_links--> skills (section-level impact)
  skills --influenced_skills(reverse)--> artifacts
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  agent TEXT NOT NULL,
  created_at REAL NOT NULL,
  content TEXT NOT NULL,
  model TEXT
);
CREATE TABLE IF NOT EXISTS evidence_edges (
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  note TEXT,
  PRIMARY KEY (artifact_id, target_type, target_ref)
);
CREATE TABLE IF NOT EXISTS skill_links (
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  skill_name TEXT NOT NULL,
  section TEXT,
  impact TEXT,
  PRIMARY KEY (artifact_id, skill_name, section)
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  skill_name TEXT,
  judgement TEXT,
  reasoning TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
  proposal_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
  voter TEXT NOT NULL,
  vote TEXT NOT NULL CHECK (vote IN ('approve', 'reject')),
  reason TEXT,
  PRIMARY KEY (proposal_id, voter)
);
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_artifact ON evidence_edges(artifact_id);
CREATE INDEX IF NOT EXISTS idx_skill_links_skill ON skill_links(skill_name);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
"""


class ArtifactStore:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- artifacts ---------------------------------------------------------
    def add_artifact(self, artifact: dict) -> None:
        now = time.time()
        self._db.execute(
            "INSERT OR REPLACE INTO artifacts (id, type, agent, created_at, content, model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artifact["artifact_id"], artifact.get("type", ""),
             artifact.get("agent", ""), artifact.get("created_at", now),
             json.dumps(artifact, ensure_ascii=False, sort_keys=True),
             artifact.get("model")))
        for link in artifact.get("evidence_links", []):
            self._db.execute(
                "INSERT OR IGNORE INTO evidence_edges (artifact_id, target_type, target_ref, note) "
                "VALUES (?, ?, ?, ?)",
                (artifact["artifact_id"], link.get("type", ""), link.get("ref", ""),
                 link.get("note")))
        self._db.commit()

    def get_artifact(self, artifact_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM artifacts WHERE id = ?",
                               (artifact_id,)).fetchone()
        if row is None:
            return None
        art = json.loads(row["content"])
        art["evidence_links"] = [
            {"type": r["target_type"], "ref": r["target_ref"], "note": r["note"]}
            for r in self._db.execute(
                "SELECT * FROM evidence_edges WHERE artifact_id = ?", (artifact_id,))]
        return art

    def artifacts(self, type: str | None = None, limit: int = 100) -> list[dict]:
        if type:
            rows = self._db.execute(
                "SELECT id, content FROM artifacts WHERE type = ? ORDER BY created_at DESC LIMIT ?",
                (type, limit))
        else:
            rows = self._db.execute(
                "SELECT id, content FROM artifacts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [json.loads(r["content"]) for r in rows]

    def count(self, type: str | None = None) -> int:
        if type:
            return self._db.execute(
                "SELECT COUNT(*) FROM artifacts WHERE type = ?", (type,)).fetchone()[0]
        return self._db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]

    # -- influenced-skills reverse index -----------------------------------
    def link_skill(self, artifact_id: str, skill_name: str, section: str | None,
                   impact: str) -> None:
        # Best-effort: a cited artifact that isn't in the store (e.g. the agent
        # hallucinated the id) simply gets no reverse link — the push outcome
        # must not depend on it. (OR IGNORE does not suppress FK violations.)
        try:
            self._db.execute(
                "INSERT INTO skill_links "
                "(artifact_id, skill_name, section, impact) VALUES (?, ?, ?, ?)",
                (artifact_id, skill_name, section, impact))
            self._db.commit()
        except sqlite3.IntegrityError:
            pass

    def influenced_skills(self, skill_name: str) -> list[dict]:
        """Reverse index: artifacts that influenced a skill."""
        return [
            {"artifact_id": r["artifact_id"], "section": r["section"],
             "impact": r["impact"]}
            for r in self._db.execute(
                "SELECT * FROM skill_links WHERE skill_name = ?", (skill_name,))]

    # -- proposals + peer review --------------------------------------------
    def add_proposal(self, proposal: dict, artifact_id: str) -> None:
        # The model's output is agent-controlled: never trust a supplied
        # status; the fleet sets status explicitly after review. Re-recording
        # the same id intentionally wipes prior votes (re-review semantics).
        self._db.execute(
            "INSERT OR REPLACE INTO proposals "
            "(id, artifact_id, kind, skill_name, judgement, reasoning, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (proposal["proposal_id"], artifact_id, proposal["kind"],
             proposal.get("skill_name"), proposal.get("judgement"),
             proposal["reasoning"], "proposed",
             proposal.get("created_at", time.time())))
        self._db.commit()

    def set_proposal_status(self, proposal_id: str, status: str) -> None:
        self._db.execute("UPDATE proposals SET status = ? WHERE id = ?",
                         (status, proposal_id))
        self._db.commit()

    def proposals(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._db.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC",
                (status,))
        else:
            rows = self._db.execute("SELECT * FROM proposals ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def add_vote(self, proposal_id: str, voter: str, vote: str, reason: str | None) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO votes (proposal_id, voter, vote, reason) "
            "VALUES (?, ?, ?, ?)",
            (proposal_id, voter, vote, reason))
        self._db.commit()

    def votes(self, proposal_id: str) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM votes WHERE proposal_id = ?", (proposal_id,))]

    # -- run state (trigger floors etc.) ------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))
        self._db.commit()
