"""taxonomy/ai-agent vocabulary validator (attested candidate).

Pure + stdlib-only: same contract as taxonomy/web-service's candidate —
reports unknown component names and template references (B-001). The
composed web-service vocabulary applies to web-facing parts at the
document level; this candidate validates the AGENT vocabulary shape.
"""

VOCABULARY = frozenset({
    "tool-runtime", "memory", "context", "guardrails", "skills-registry",
    "mcp-transport", "vector-store", "evals",
})
TEMPLATE_IDS = frozenset({"S-001", "S-002", "S-003", "S-004", "S-005"})


def validate_against(components, template_refs=()):
    """Return a list of error strings; empty list == conformant shape."""
    errors = []
    if not isinstance(components, dict):
        return ["components must be a dict"]
    for name in components:
        if name not in VOCABULARY:
            errors.append(f"unknown component: {name}")
    for ref in template_refs or ():
        if ref not in TEMPLATE_IDS:
            errors.append(f"unknown template reference: {ref}")
    return errors
