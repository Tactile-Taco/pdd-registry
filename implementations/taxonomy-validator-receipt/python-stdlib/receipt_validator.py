"""taxonomy/validator-receipt shape validator (attested candidate).

Pure + stdlib-only: validates a validator-loop execution receipt against
the three provider shapes (github-actions-run, generic-ci,
local-attestation). Reports unknown providers, missing/invalid required
fields, invalid conclusions, and malformed digests (B-001). The registry
parses signed_object.validator_receipt with this validator and reports
validity as an observation — receipts stay optional (S-007 additive).
"""

import re

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
URL_RE = re.compile(r"^https?://")
CONCLUSIONS = {"success", "failure", "cancelled"}

PROVIDERS = {
    "github-actions-run": {
        "required": ("repository", "run_id", "workflow", "conclusion",
                     "started_at", "artifacts"),
        "checks": ("repository", "run_id", "workflow", "conclusion",
                   "started_at", "artifacts"),
    },
    "generic-ci": {
        "required": ("pipeline_url", "conclusion", "started_at", "artifacts"),
        "checks": ("pipeline_url", "conclusion", "started_at", "artifacts"),
    },
    "local-attestation": {
        "required": ("tool", "tool_version", "timestamp", "artifact_digests"),
        "checks": ("tool", "tool_version", "timestamp", "artifact_digests"),
    },
}


def validate_receipt(receipt):
    """Return a list of error strings; empty list == valid shape."""
    errors = []
    if not isinstance(receipt, dict):
        return ["receipt must be a dict"]
    provider = receipt.get("provider")
    if provider not in PROVIDERS:
        return [f"unknown provider: {provider!r}"]
    shape = PROVIDERS[provider]
    for field in shape["required"]:
        if field not in receipt:
            errors.append(f"missing required field: {field}")
    if "conclusion" in receipt and receipt["conclusion"] not in CONCLUSIONS:
        errors.append(f"invalid conclusion: {receipt['conclusion']!r}")
    if "repository" in receipt and not REPO_RE.fullmatch(receipt["repository"]):
        errors.append("invalid repository (expected owner/name)")
    if "run_id" in receipt and not isinstance(receipt["run_id"], int):
        errors.append("run_id must be an integer")
    for key in ("started_at", "timestamp"):
        if key in receipt and not ISO_DATE_RE.match(receipt[key]):
            errors.append(f"invalid {key} (expected ISO-8601 start)")
    if "pipeline_url" in receipt and not URL_RE.match(receipt["pipeline_url"]):
        errors.append("invalid pipeline_url (expected http(s)://)")
    if "tool" in receipt and not isinstance(receipt["tool"], str):
        errors.append("tool must be a string")
    if "tool_version" in receipt and not isinstance(receipt["tool_version"], str):
        errors.append("tool_version must be a string")
    artifacts = receipt.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list) or not artifacts:
            errors.append("artifacts must be a non-empty list")
        else:
            for art in artifacts:
                if not isinstance(art, dict):
                    errors.append("artifact entries must be objects")
                    continue
                if "name" not in art or not art["name"]:
                    errors.append("artifact entry missing name")
                dg = art.get("digest")
                if not isinstance(dg, str) or not SHA256_RE.fullmatch(dg or ""):
                    errors.append(f"invalid artifact digest: {dg!r}")
    digests = receipt.get("artifact_digests")
    if digests is not None:
        if not isinstance(digests, list) or not digests:
            errors.append("artifact_digests must be a non-empty list")
        else:
            for dg in digests:
                if not isinstance(dg, str) or not SHA256_RE.fullmatch(dg or ""):
                    errors.append(f"invalid artifact digest: {dg!r}")
    return errors
