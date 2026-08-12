"""Skill push tests: frontmatter + Provenance writing, git commit/push to a
bare remote, edit appends, dry-run writes nothing."""

from __future__ import annotations

import subprocess

from conftest import good_proposal

from memory_plane.push import SkillRepo


def _remote_head(skills_repo) -> str | None:
    out = subprocess.run(["git", "-C", str(skills_repo), "ls-remote", "origin", "main"],
                         capture_output=True, text=True, check=True)
    parts = out.stdout.split()
    return parts[0] if parts else None


def test_new_skill_writes_frontmatter_and_provenance(skills_repo):
    repo = SkillRepo(str(skills_repo))
    p = good_proposal()
    p["proposal_id"] = "p-new"
    result = repo.apply(p)
    path = skills_repo / "skills" / "fleet-probe-skill" / "SKILL.md"
    content = path.read_text()
    assert content.startswith("---\nname: fleet-probe-skill")
    assert "description:" in content.split("---")[1]
    assert "## Provenance" in content
    assert "- cs-1 — tooling friction" in content
    assert result["commit"]
    assert _remote_head(skills_repo) == result["commit"]
    # grounded skill link bookkeeping happens in fleet, but the repo side is
    # verifiable via the commit message
    msg = subprocess.run(["git", "-C", str(skills_repo), "log", "-1", "--format=%s"],
                         capture_output=True, text=True, check=True).stdout
    assert "fleet proposal" in msg


def test_edit_skill_appends_and_preserves_frontmatter(skills_repo):
    p = {"kind": "edit-skill", "skill_name": "existing-skill",
         "body": "Add a section about caching.",
         "motivated_by": [{"artifact_id": "cs-2", "impact": "cache misses"}],
         "reasoning": "recurring", "proposal_id": "p-edit"}
    SkillRepo(str(skills_repo)).apply(p)
    content = (skills_repo / "skills" / "existing-skill" / "SKILL.md").read_text()
    assert content.startswith("---\nname: existing-skill")
    assert "## Update (" in content
    assert "Add a section about caching." in content
    assert "## Provenance" in content


def test_edit_skill_missing_target_fails(skills_repo):
    p = {"kind": "edit-skill", "skill_name": "nope-skill", "body": "x",
         "motivated_by": [], "reasoning": "r", "proposal_id": "p-missing"}
    repo = SkillRepo(str(skills_repo))
    try:
        repo.apply(p)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass


def test_dry_run_writes_nothing(skills_repo):
    repo = SkillRepo(str(skills_repo), dry_run=True)
    p = good_proposal()
    p["proposal_id"] = "p-dry"
    plan = repo.apply(p)
    assert plan["dry_run"] is True
    assert not (skills_repo / "skills" / "fleet-probe-skill").exists()
    log = subprocess.run(["git", "-C", str(skills_repo), "log", "--oneline"],
                         capture_output=True, text=True, check=True).stdout
    assert len(log.splitlines()) == 1  # only the init commit


def test_plan_returns_rendered_content_without_writing(skills_repo):
    repo = SkillRepo(str(skills_repo))
    p = good_proposal()
    p["proposal_id"] = "p-plan"
    plan = repo.plan(p)
    assert plan["action"] == "create"
    assert "## Provenance" in plan["content"]
    assert not (skills_repo / "skills" / "fleet-probe-skill").exists()
