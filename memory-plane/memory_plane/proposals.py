"""Skill-improvement proposal model + validation.

A proposal is the output of the synthesis step (see agent_defs.SYNTHESIS_STEP).
Grounding rule (design): any new/edit skill must cite the artifact(s) that
motivated it; a no-proposal verdict must carry a disciplined judgement that
the pain is naturally challenging and a concrete fix would be counterproductive.
"""

from __future__ import annotations

import re

KINDS = {"new-skill", "edit-skill", "process-skill", "no-proposal"}
JUDGEMENTS = {"concrete-fix", "naturally-hard"}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(?:[^\n]*\n)*?---\s*\n", re.MULTILINE)


def validate_proposal(p: dict) -> list[str]:
    """Return a list of violations (empty = valid)."""
    errs: list[str] = []

    kind = p.get("kind")
    if kind not in KINDS:
        errs.append(f"kind must be one of {sorted(KINDS)}, got {kind!r}")

    reasoning = p.get("reasoning")
    if not reasoning or not reasoning.strip():
        errs.append("reasoning is required and must be non-empty")

    motivated_by = p.get("motivated_by") or []
    if kind in ("new-skill", "edit-skill"):
        if not p.get("skill_name"):
            errs.append("new-skill/edit-skill requires skill_name")
        if not motivated_by or not all(
                m.get("artifact_id") and m.get("impact") for m in motivated_by):
            errs.append("new-skill/edit-skill requires motivated_by "
                        "entries with artifact_id and impact (grounding rule)")
        body = p.get("body") or ""
        fm = _FRONTMATTER_RE.match(body)
        if kind == "new-skill":
            if not fm:
                errs.append("new-skill body must start with frontmatter "
                            "(name: + description:)")
            else:
                head = body[: fm.end()]
                if not re.search(r"^name\s*:", head, re.M):
                    errs.append("frontmatter must contain a name: field")
                if not re.search(r"^description\s*:", head, re.M):
                    errs.append("frontmatter must contain a description: field")
            if "## Provenance" not in body:
                errs.append("new-skill body must contain a '## Provenance' "
                            "section citing the motivating artifacts")
        else:  # edit-skill
            if "## Provenance" not in body and not p.get("preserve_existing", True):
                errs.append("edit-skill body should carry '## Provenance'")
    elif kind == "no-proposal":
        if p.get("judgement") != "naturally-hard":
            errs.append("no-proposal requires judgement == 'naturally-hard'")
    else:  # process-skill
        if not p.get("body") and not p.get("description"):
            errs.append("process-skill requires a body or description")

    judgement = p.get("judgement")
    if judgement is not None and judgement not in JUDGEMENTS:
        errs.append(f"judgement must be one of {sorted(JUDGEMENTS)}, got {judgement!r}")

    return errs


def proposal_is_valid(p: dict) -> bool:
    return not validate_proposal(p)


def extract_proposals(artifact: dict) -> list[dict]:
    """Pull skill_proposals out of a reflection/retrospective artifact,
    assigning each a stable proposal_id."""
    raw = artifact.get("skill_proposals") or []
    out = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            continue
        p = dict(p)
        p["proposal_id"] = p.get("proposal_id") or f"{artifact['artifact_id']}-p{i + 1}"
        p["artifact_id"] = artifact["artifact_id"]
        out.append(p)
    return out
