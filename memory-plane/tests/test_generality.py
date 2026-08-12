"""The skill-generality principle must be present wherever the fleet produces
or evaluates skill-improvement proposals.

Project-specific operational detail belongs in case studies/retrospectives
(which are referenced + provenanced), NOT in general-purpose skills.
"""

from memory_plane import agent_defs, review

GENERALITY_AGENTS = {
    "agent-reflection",
    "agent-retrospective",
    "agent-meta",
    "agent-case-study-curator",
}
# meta-agent carries the principle inline in its standing process
META_INLINE = "GENERAL-PURPOSE"


def _system(a):
    return agent_defs.agent_def(a)["system"]


def test_generality_principle_in_reflection_and_retrospective():
    for a in ("agent-reflection", "agent-retrospective"):
        sys_prompt = _system(a)
        assert "GENERAL-PURPOSE" in sys_prompt
        assert "provenanced" in sys_prompt
        assert "case study/retrospective" in sys_prompt or "case studies and retrospectives" in sys_prompt


def test_generality_in_meta_and_case_study():
    assert META_INLINE in _system("agent-meta")
    # the case-study curator is the *home* for project-specific detail
    assert "GENERAL-PURPOSE" not in _system("agent-case-study-curator")
    assert "CORRECT home for project-specific" in _system("agent-case-study-curator")


def test_review_prompt_rejects_project_specific_skill_edits():
    tpl = review.REVIEW_TEMPLATE
    assert "project-specific operational detail" in tpl
    assert "general" in tpl and "purpose skill" in tpl  # wraps across a line break
    assert "case studies/retrospectives" in tpl
