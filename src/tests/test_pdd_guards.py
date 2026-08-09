"""Key-free unit tests for the scripts/pdd.py evidence gates.

The evidence build path takes an unauthenticated validation-results file and
derives filenames from its digests (security review findings). These tests pin
the guards WITHOUT needing PDD_EVIDENCE_KEY:

- `_valid_sha256`: only the exact sha256:<64 lowercase hex> shape passes, so a
  hostile digest can never inject path separators into evidence filenames.
- `_bundle_digest`: byte-for-byte parity with the Validator Loop's digest
  (validators/validate_candidate.py), so the stale-results gate compares like
  with like; and it is sensitive to any bundle change.
- `_load_validation_results`: every malformed results-file shape fails closed
  with a clean SystemExit before any value can be used.

Run: python3 -m pytest src/tests/test_pdd_guards.py -q
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("pdd_cli", ROOT / "scripts" / "pdd.py")
pdd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdd)

sys.path.insert(0, str(ROOT / "validators"))  # validate_candidate import
import validate_candidate as vc  # noqa: E402


def test_valid_sha256_accepts_only_exact_digests():
    ok = "sha256:" + "a" * 64
    assert pdd._valid_sha256(ok)
    for bad in (None, "", 123, "sha256:" + "A" * 64, "sha256:" + "a" * 63,
                "sha256:" + "a" * 65, "md5:" + "a" * 64, "sha256:" + "g" * 64,
                "sha256:../evil", "sha256:" + "a" * 64 + "\n",
                "../../etc/passwd", "sha256:" + "a" * 64 + "/x"):
        assert not pdd._valid_sha256(bad), f"{bad!r} must be rejected"


def test_bundle_digest_parity_with_validator():
    """The stale-results gate compares pdd's recomputed digest against the
    digest attested by validation results; both must be computed identically."""
    import validate_candidate as vc  # noqa: E402 (same src/tests sys.path)

    for name in ("pdd-registry", "user-registry"):
        bdir = ROOT / "pdd-bundles" / name
        assert pdd._bundle_digest(bdir) == vc.bundle_digest(bdir), name


def test_bundle_digest_is_sensitive_to_bundle_changes(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    proto = bdir / "protocol.yaml"
    proto.write_text("protocol:\n  name: x\n  version: 1.0.0\n  status: draft\n")
    d1 = pdd._bundle_digest(bdir)
    proto.write_text("protocol:\n  name: x\n  version: 1.1.0\n  status: draft\n")
    d2 = pdd._bundle_digest(bdir)
    assert d1.startswith("sha256:") and d1 != d2
    assert hashlib.sha256(proto.read_bytes()).hexdigest() != d1.split(":")[1]


def test_behavioral_coverage_splits_covered_and_uncovered():
    """The behavioral pass label may only claim invariant_lineage-covered ids;
    uncovered ids (e.g. B-006 publish idempotency) must land in the skip set —
    a pass label must never imply enforcement that does not exist."""
    lineage = {"B-001": ["test_b001"], "B-002": ["test_b002"], "B-006": None}
    covered, uncovered = vc._behavioral_coverage(
        ["B-001", "B-002", "B-003", "B-004", "B-005", "B-006"], lineage)
    assert covered == ["B-001", "B-002"]
    assert uncovered == ["B-003", "B-004", "B-005", "B-006"]
    # empty lineage -> everything uncovered; B-* label path
    covered, uncovered = vc._behavioral_coverage(["B-001"], {})
    assert covered == [] and uncovered == ["B-001"]


def test_evidence_validation_resource_format():
    """--validation-resource must be an http(s) URL or urn: URN (S-007) —
    the guard regex (shared with the publish schema pattern) must accept
    real record URLs and reject anything else."""
    ok = ("https://github.com/Tactile-Taco/pdd-repository/actions/runs/1",
          "https://ci.example/x?y=1#frag", "urn:pdd:run:42")
    for url in ok:
        assert re.fullmatch(r"(https?://|urn:)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", url), url
    for bad in ("", "nope", "ftp://x", "file:///etc/passwd", "javascript:alert(1)"):
        assert not re.fullmatch(r"(https?://|urn:)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", bad), bad


# --- _load_validation_results: fail-closed shape checks --------------------


def _valid_results() -> dict:
    return {"candidate_digest": "sha256:" + "b" * 64, "verdict": "admit",
            "protocol": {"bundle_digest": "sha256:" + "c" * 64},
            "validators": [], "results": []}


def test_load_validation_results_rejects_corrupt_json(tmp_path):
    f = tmp_path / "results.json"
    f.write_text("{broken")
    with pytest.raises(SystemExit):
        pdd._load_validation_results(f)


def test_load_validation_results_rejects_non_object_root(tmp_path):
    f = tmp_path / "results.json"
    f.write_text("[]")
    with pytest.raises(SystemExit):
        pdd._load_validation_results(f)


def test_load_validation_results_rejects_missing_verdict(tmp_path):
    f = tmp_path / "results.json"
    data = _valid_results()
    del data["verdict"]
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        pdd._load_validation_results(f)


def test_load_validation_results_rejects_non_admit_verdict(tmp_path):
    f = tmp_path / "results.json"
    data = _valid_results()
    data["verdict"] = "reject"
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        pdd._load_validation_results(f)


def test_load_validation_results_rejects_malformed_sections(tmp_path):
    f = tmp_path / "results.json"
    for key, bad in (("protocol", []), ("validators", {}), ("results", {})):
        data = _valid_results()
        data[key] = bad
        f.write_text(json.dumps(data))
        with pytest.raises(SystemExit):
            pdd._load_validation_results(f)


def test_load_validation_results_accepts_valid_shape(tmp_path):
    f = tmp_path / "results.json"
    f.write_text(json.dumps(_valid_results()))
    assert pdd._load_validation_results(f)["verdict"] == "admit"


# --- cmd_evidence_build: legacy migration fails closed ----------------------


def _scaffold_build(tmp_path):
    """Minimal repo layout for cmd_evidence_build: bundle, impl, validation
    results (valid shape, real digests), ledger, and a legacy-named admission
    file. Returns (bundle_root, impl_root)."""
    bundles = tmp_path / "pdd-bundles"
    evidence = tmp_path / "evidence"
    bdir = bundles / "test-bundle"
    bdir.mkdir(parents=True)
    (bdir / "protocol.yaml").write_text(
        "protocol:\n  name: test-bundle\n  version: 1.1.0\n  status: draft\n"
        "namespace: test\n")
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "candidate-manifest.json").write_text(
        json.dumps({"entry_module": "impl_mod"}))
    (impl / "impl_mod.py").write_text("VALUE = 1\n")
    impl_digest = "sha256:" + hashlib.sha256((impl / "impl_mod.py").read_bytes()).hexdigest()
    bdigest = pdd._bundle_digest(bdir)
    val = evidence / "test-bundle" / "validation"
    val.mkdir(parents=True)
    (val / f"{impl_digest.split(':')[1][:12]}.results.json").write_text(json.dumps({
        "candidate_digest": impl_digest, "verdict": "admit",
        "protocol": {"bundle_digest": bdigest}, "validators": [], "results": []}))
    ledger = evidence / "test-bundle" / "runtime-ledger.jsonl"
    ledger.write_text(json.dumps({"observations": {
        "admission": impl_digest, "evidence_digest": "sha256:" + "f" * 64}}))
    adm = evidence / "test-bundle" / "admission"
    adm.mkdir(parents=True)
    return bundles, impl


def test_evidence_build_rejects_corrupt_legacy_no_rename(tmp_path, monkeypatch):
    """A legacy admission file with a non-dict protocol section must fail
    closed (rc=1) and never be renamed or overwritten."""
    bundles, impl = _scaffold_build(tmp_path)
    legacy = tmp_path / "evidence" / "test-bundle" / "admission"
    impl_digest_16 = hashlib.sha256((impl / "impl_mod.py").read_bytes()).hexdigest()[:16]
    legacy_file = legacy / f"{impl_digest_16}.evidence.json"
    legacy_file.write_text(json.dumps({"protocol": ["not", "a", "dict"]}))

    monkeypatch.setattr(pdd, "BUNDLES", bundles)
    monkeypatch.setattr(pdd, "EVIDENCE", tmp_path / "evidence")
    rc = pdd.cmd_evidence_build(
        ["test-bundle", "--impl", str(impl)])
    assert rc == 1  # clean FAIL, not a traceback
    assert legacy_file.exists()  # not renamed, not overwritten
    # no stem-keyed file was created either
    assert list(legacy.glob("*.evidence.json")) == [legacy_file]


def test_evidence_build_rejects_non_dict_results_no_rename(tmp_path, monkeypatch):
    """A non-object validation-results file must fail closed before any
    evidence file is touched."""
    bundles, impl = _scaffold_build(tmp_path)
    val_file = next((tmp_path / "evidence" / "test-bundle" / "validation").glob("*.results.json"))
    val_file.write_text("[]")

    monkeypatch.setattr(pdd, "BUNDLES", bundles)
    monkeypatch.setattr(pdd, "EVIDENCE", tmp_path / "evidence")
    with pytest.raises(SystemExit):  # fail-closed: sys.exit before any use
        pdd.cmd_evidence_build(["test-bundle", "--impl", str(impl)])
    assert list((tmp_path / "evidence" / "test-bundle" / "admission").glob("*.evidence.json")) == []
