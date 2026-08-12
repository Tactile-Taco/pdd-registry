"""Proposal validation tests: grounding rule, frontmatter, disciplined
no-proposal verdicts, process skills."""

from __future__ import annotations

from conftest import good_proposal

from memory_plane.proposals import validate_proposal


def test_valid_new_skill():
    assert validate_proposal(good_proposal()) == []


def test_ungrounded_new_skill_rejected():
    p = good_proposal()
    p["motivated_by"] = []
    errs = validate_proposal(p)
    assert any("motivated_by" in e for e in errs)


def test_missing_frontmatter_rejected():
    p = good_proposal()
    p["body"] = "no frontmatter here\n\n## Provenance\n- cs-1 — impact"
    errs = validate_proposal(p)
    assert any("frontmatter" in e for e in errs)


def test_frontmatter_requires_name_and_description():
    p = good_proposal()
    p["body"] = ("---\ndescription: Only description.\n---\n\nBody\n\n"
                 "## Provenance\n- cs-1 — impact")
    errs = validate_proposal(p)
    assert any("name" in e for e in errs)


def test_new_skill_requires_provenance_section():
    p = good_proposal()
    p["body"] = ("---\nname: x\ndescription: y\n---\n\nBody without provenance")
    errs = validate_proposal(p)
    assert any("Provenance" in e for e in errs)


def test_no_proposal_requires_naturally_hard_judgement():
    p = {"kind": "no-proposal", "judgement": "concrete-fix",
         "motivated_by": [], "reasoning": "it is fine"}
    assert any("naturally-hard" in e for e in validate_proposal(p))

    p["judgement"] = "naturally-hard"
    assert validate_proposal(p) == []


def test_no_proposal_requires_reasoning():
    p = {"kind": "no-proposal", "judgement": "naturally-hard",
         "motivated_by": [], "reasoning": "   "}
    assert any("reasoning" in e for e in validate_proposal(p))


def test_edit_skill_needs_name_and_grounding():
    p = {"kind": "edit-skill", "skill_name": "web-perf",
         "motivated_by": [{"artifact_id": "cs-2", "impact": "regression"}],
         "reasoning": "found a recurring regression pattern",
         "body": "Add a section about XX."}
    assert validate_proposal(p) == []

    p["skill_name"] = ""
    assert any("skill_name" in e for e in validate_proposal(p))


def test_process_skill_requires_body_or_description():
    p = {"kind": "process-skill", "motivated_by": [],
         "reasoning": "the fleet lacks a review checklist"}
    assert any("body or description" in e for e in validate_proposal(p))

    p["description"] = "Add a review checklist to the fleet process."
    assert validate_proposal(p) == []


def test_bad_kind_rejected():
    p = {"kind": "skill-bomb", "motivated_by": [], "reasoning": "x"}
    assert any("kind" in e for e in validate_proposal(p))


def test_path_traversal_skill_names_rejected():
    for bad in ("../../.ssh/authorized_keys", "/etc/passwd", "a/b", "..",
                "web-perf\\evil", "a b"):
        p = good_proposal()
        p["skill_name"] = bad
        assert any("skill_name" in e for e in validate_proposal(p)), bad
    # valid names still pass
    for ok in ("web-perf", "my.skill_2", "a-b"):
        p = good_proposal()
        p["skill_name"] = ok
        assert validate_proposal(p) == [], ok
