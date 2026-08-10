"""taxonomy/web-service vocabulary validator (attested candidate).

Pure + stdlib-only: given a component map and template references,
reports unknown component names and unknown template ids. The taxonomy
itself does NOT enforce conformance of concrete bundles — it validates
the vocabulary SHAPE (B-001).
"""

VOCABULARY = frozenset({
    "ingress", "api", "authn", "authorization", "database", "cache",
    "queue", "storage", "observability", "config", "scheduler", "worker",
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
