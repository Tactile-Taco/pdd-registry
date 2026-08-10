"""Contract tests for the taxonomy/validator-receipt shape validator."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from receipt_validator import validate_receipt  # noqa: E402


def _gh(**over):
    r = {"provider": "github-actions-run", "repository": "a/b",
         "run_id": 42, "workflow": "pdd-validator-loop",
         "conclusion": "success", "started_at": "2026-08-10T00:00:00Z",
         "artifacts": [{"name": "results", "digest": "sha256:" + "a" * 64}]}
    r.update(over)
    return r


def _generic(**over):
    r = {"provider": "generic-ci", "pipeline_url": "https://ci.example/1",
         "conclusion": "failure", "started_at": "2026-08-10T00:00:00Z",
         "artifacts": [{"name": "out", "digest": "sha256:" + "b" * 64}]}
    r.update(over)
    return r


def _local(**over):
    r = {"provider": "local-attestation", "tool": "pdd-validator",
         "tool_version": "1.0.0", "timestamp": "2026-08-10T00:00:00Z",
         "artifact_digests": ["sha256:" + "c" * 64]}
    r.update(over)
    return r


def test_B001_valid_receipts_for_all_providers():
    assert validate_receipt(_gh()) == []
    assert validate_receipt(_generic()) == []
    assert validate_receipt(_local()) == []


def test_B001_unknown_provider_reported():
    errors = validate_receipt({"provider": "gitlab-ci"})
    assert errors and "unknown provider" in errors[0]


def test_B001_missing_required_fields_reported():
    errors = validate_receipt({"provider": "github-actions-run"})
    assert any("missing required field" in e for e in errors)
    assert len(errors) >= 6


def test_B001_invalid_conclusion_reported():
    errors = validate_receipt(_gh(conclusion="maybe"))
    assert any("invalid conclusion" in e for e in errors)


def test_B001_malformed_digests_reported():
    errors = validate_receipt(_gh(artifacts=[{"name": "x", "digest": "nope"}]))
    assert any("invalid artifact digest" in e for e in errors)
    errors = validate_receipt(_local(artifact_digests=["sha256:short"]))
    assert any("invalid artifact digest" in e for e in errors)


def test_B001_non_dict_receipt_fails():
    assert validate_receipt(None) == ["receipt must be a dict"]
    assert validate_receipt("x") == ["receipt must be a dict"]


def test_B001_deterministic_and_non_mutating():
    r = _gh(conclusion="bad")
    first = validate_receipt(r)
    second = validate_receipt(r)
    assert first == second and r["conclusion"] == "bad"
