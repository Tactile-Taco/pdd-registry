"""Tests for the pdd service verification surface (src/server.py).

Run with: python3 -m pytest src/tests -q  (or `make test`, which includes it).
The committed evidence chain is signed with the PDD_EVIDENCE_KEY held in
Infisical (nixos-infra, prod); the fixture uses that key (from env if the
runner already exports it, else fetched via the Infisical CLI) so the tests
prove the committed chain verifies under the real signing key.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INFISICAL_PROJECT = "7a2f10fc-2d47-4008-a817-3f5493dc7476"

_spec = importlib.util.spec_from_file_location("pdd_server", ROOT / "src" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
server.ROOT = ROOT
server.SKILLS = ROOT / ".reasonix" / "skills"
server.BUNDLES = ROOT / "pdd-bundles"
server.EVIDENCE = ROOT / "evidence"


@pytest.fixture(autouse=True)
def _evidence_key():
    key = os.environ.get("PDD_EVIDENCE_KEY")
    if not key:
        out = subprocess.run(
            ["infisical", "secrets", "get", "PDD_EVIDENCE_KEY",
             "--projectId", INFISICAL_PROJECT, "--env", "prod",
             "--plain", "--silent"],
            capture_output=True, text=True)
        if out.returncode == 0:
            key = out.stdout.strip()
    if not key:
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail("no PDD_EVIDENCE_KEY in CI — is the repository secret set?")
        pytest.skip("no PDD_EVIDENCE_KEY available (set env or run `infisical login`)")
    os.environ["PDD_EVIDENCE_KEY"] = key
    yield


def test_admission_verified_true_on_committed_evidence():
    res = server._admission("user-registry")
    assert len(res) == 1
    row = res[0]
    assert row["signature_valid"] is True
    assert row["ledger_valid"] is True
    assert row["ledger_attested"] is True
    assert row["decision"] == "attest-pass"
    assert row["verified"] is True


def test_admission_tampered_evidence_fails_closed(tmp_path):
    # Copy the real evidence tree, tamper the admission object, and point the
    # server at the copy: verified must become False (signature breaks).
    src = ROOT / "evidence" / "user-registry"
    dst = tmp_path / "evidence" / "user-registry"
    for p in src.rglob("*"):
        if p.is_file():
            rel = p.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(p.read_bytes())
    tampered = dst / "admission" / "5614cd8f49224f28.evidence.json"
    data = json.loads(tampered.read_text())
    data["decision"] = "forged"  # changes the signed body
    tampered.write_text(json.dumps(data))

    server.EVIDENCE = tmp_path / "evidence"
    try:
        res = server._admission("user-registry")
    finally:
        server.EVIDENCE = ROOT / "evidence"
    assert len(res) == 1
    row = res[0]
    assert row["signature_valid"] is False
    assert row["verified"] is False
    # The real ledger still verifies and genuinely attested that artifact
    # digest — what broke is the forged file's signature, so verified is False.
    assert row["ledger_valid"] is True
    assert row["ledger_attested"] is True


def test_admission_unattested_file_reports_false(tmp_path):
    # A validly-signed-looking object not present in the ledger must not verify.
    src = ROOT / "evidence" / "user-registry" / "admission" / "5614cd8f49224f28.evidence.json"
    dst = tmp_path / "evidence" / "user-registry" / "admission"
    dst.mkdir(parents=True)
    data = json.loads(src.read_text())
    data["implementation"]["artifact_digest"] = "sha256:deadbeef"  # not in ledger
    (dst / "unattested.evidence.json").write_text(json.dumps(data))
    # no ledger in the temp tree -> ledger_valid False -> verified False
    server.EVIDENCE = tmp_path / "evidence"
    try:
        res = server._admission("user-registry")
    finally:
        server.EVIDENCE = ROOT / "evidence"
    assert len(res) == 1
    assert res[0]["ledger_valid"] is False
    assert res[0]["verified"] is False
