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


def test_skill_name_traversal_blocked_defense_in_depth(skills_repo):
    repo = SkillRepo(str(skills_repo))
    for bad in ("../../escape", "/etc/passwd"):
        p = good_proposal()
        p["proposal_id"] = f"p-{bad.replace('/', '_')}"
        p["skill_name"] = bad
        try:
            repo.apply(p)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass
    # nested-but-not-escaping names are rejected at the validation layer
    # (proposals.SKILL_NAME_RE), not by the path check
    assert not (skills_repo.parent / "escape").exists()


def test_reapply_after_failed_push_is_idempotent(skills_repo, tmp_path):
    """A failed push leaves the commit locally; re-approval must not duplicate
    the change."""
    p = good_proposal()
    p["proposal_id"] = "p-retry"
    repo = SkillRepo(str(skills_repo))

    # Make the remote reject pushes: a non-bare repo with main checked out.
    nonbare = tmp_path / "skills-nonbare"
    subprocess.run(["git", "init", "-q", "-b", "main", str(nonbare)], check=True)
    subprocess.run(["git", "-C", str(nonbare), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(nonbare), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(skills_repo), "remote", "set-url",
                    "origin", str(nonbare)], check=True)
    try:
        repo.apply(p)
        raise AssertionError("expected push rejection")
    except RuntimeError:
        pass  # push failed; local commit remains

    # Restore the working bare remote and re-apply (as a re-review would).
    bare = tmp_path / "skills-remote.git"
    subprocess.run(["git", "-C", str(skills_repo), "remote", "set-url",
                    "origin", str(bare)], check=True)
    result = repo.apply(p)
    assert result["action"] == "already-applied"
    content = (skills_repo / "skills" / "fleet-probe-skill" / "SKILL.md").read_text()
    assert content.count("## Provenance") == 1  # no duplicate section
    log = subprocess.run(["git", "-C", str(skills_repo), "log", "--oneline"],
                         capture_output=True, text=True, check=True).stdout
    assert log.count("fleet proposal p-retry") == 1  # one commit, not two
    assert _remote_head(skills_repo) == repo._git("rev-parse", "HEAD").strip()


def test_reapply_with_stacked_commit_no_duplication(skills_repo, tmp_path):
    """A later proposal committed on top of the failed push must not confuse
    re-approval: the marker is searched across history, and re-apply must not
    duplicate sections or wedge."""
    p1 = good_proposal()
    p1["proposal_id"] = "p-stack-1"
    repo = SkillRepo(str(skills_repo))

    # first apply: push fails (non-bare checked-out remote), commit stays
    nonbare = tmp_path / "skills-nonbare2"
    subprocess.run(["git", "init", "-q", "-b", "main", str(nonbare)], check=True)
    subprocess.run(["git", "-C", str(nonbare), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(nonbare), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(skills_repo), "remote", "set-url",
                    "origin", str(nonbare)], check=True)
    try:
        repo.apply(p1)
        raise AssertionError("expected push rejection")
    except RuntimeError:
        pass

    # a second, independent proposal commits on top (this push succeeds)
    p2 = good_proposal()
    p2["proposal_id"] = "p-stack-2"
    p2["skill_name"] = "stacked-skill-2"
    bare = tmp_path / "skills-remote.git"
    subprocess.run(["git", "-C", str(skills_repo), "remote", "set-url",
                    "origin", str(bare)], check=True)
    repo.apply(p2)

    # re-approval of p1: marker found in history -> no duplicate, remote converges
    result = repo.apply(p1)
    assert result["action"] == "already-applied"
    content = (skills_repo / "skills" / "fleet-probe-skill" / "SKILL.md").read_text()
    assert content.count("## Provenance") == 1
    log = subprocess.run(["git", "-C", str(skills_repo), "log", "--oneline"],
                         capture_output=True, text=True, check=True).stdout
    assert log.count("fleet proposal p-stack-1") == 1
    assert log.count("fleet proposal p-stack-2") == 1
    assert _remote_head(skills_repo) == repo._git("rev-parse", "HEAD").strip()
