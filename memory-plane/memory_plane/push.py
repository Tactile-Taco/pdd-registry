"""Skill push mechanics against the canonical skills repo.

Grounding rule: any pushed skill carries a '## Provenance' section citing the
artifact(s) that motivated it. Frontmatter (name + description) is mandatory
for new skills per the canonicalizer pipeline. Git is the audit trail: every
push is a commit on the skills repo (origin push; dry_run skips all writes).

Only new-skill / edit-skill proposals land in the canonical repo. Process-skill
proposals update fleet memory, NOT the repo (see memory-plane-design.md).
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess

_FRONTMATTER = "---\nname: {name}\ndescription: {description}\n---\n\n"


def _provenance_block(motivated_by: list[dict]) -> str:
    lines = ["## Provenance", ""]
    for m in motivated_by:
        lines.append(f"- {m.get('artifact_id', '?')} — {m.get('impact', '')}")
    return "\n".join(lines) + "\n"


class SkillRepo:
    def __init__(self, repo_dir: str, *, dry_run: bool = False,
                 branch: str | None = None) -> None:
        self.repo_dir = repo_dir
        self.dry_run = dry_run
        self.branch = branch or "main"

    def _git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", "-C", self.repo_dir, *args],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr[:400]}")
        return out.stdout

    def skill_path(self, skill_name: str) -> str:
        return os.path.join(self.repo_dir, "skills", skill_name, "SKILL.md")

    def _render_new(self, p: dict) -> str:
        body = p.get("body") or ""
        body = re.sub(r"^---\s*\n(?:[^\n]*\n)*?---\s*\n", "", body)  # strip any
        # embedded frontmatter; we own the canonical one
        return (_FRONTMATTER.format(name=p["skill_name"],
                                    description=p.get("description") or p["skill_name"])
                + body.strip() + "\n\n" + _provenance_block(p.get("motivated_by") or []))

    def _render_edit(self, p: dict) -> str:
        path = self.skill_path(p["skill_name"])
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"edit-skill target not found: {path} (skill may not exist yet)")
        with open(path, encoding="utf-8") as f:
            current = f.read()
        today = datetime.date.today().isoformat()
        section = (f"\n## Update ({today})\n\n{p.get('body', '').strip()}\n\n"
                   + _provenance_block(p.get("motivated_by") or []) + "\n")
        return current.rstrip() + "\n" + section

    def plan(self, p: dict) -> dict:
        """What would change (no writes)."""
        kind = p.get("kind")
        if kind == "new-skill":
            return {"action": "create",
                    "path": self.skill_path(p["skill_name"]),
                    "content": self._render_new(p)}
        if kind == "edit-skill":
            return {"action": "append",
                    "path": self.skill_path(p["skill_name"]),
                    "content": self._render_edit(p)}
        raise ValueError(f"SkillRepo handles new-skill/edit-skill only, got {kind!r}")

    def apply(self, p: dict) -> dict:
        if self.dry_run:
            return {"dry_run": True, **self.plan(p)}
        plan = self.plan(p)
        path = plan["path"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(plan["content"])
        self._git("add", "skills")
        summary = (p.get("title") or p.get("description") or p["kind"])[:120]
        self._git("commit", "-m",
                  f"skill({p['kind']}): {summary} [fleet proposal {p['proposal_id']}]")
        self._git("push", "origin", self.branch)
        return {"dry_run": False, "action": plan["action"], "path": path,
                "commit": self._git("rev-parse", "HEAD").strip()}
