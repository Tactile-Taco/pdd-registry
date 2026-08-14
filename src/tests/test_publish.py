"""Tests for the POST /publish surface (src/server.py).

The endpoint is content-addressed + SQLite-idempotent; evidence is signed
with the same PDD_EVIDENCE_KEY the committed registry evidence uses.
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from pdd import evidence as pdd_evidence  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INFISICAL_PROJECT = "7a2f10fc-2d47-4008-a817-3f5493dc7476"

_spec = importlib.util.spec_from_file_location("pdd_server", ROOT / "src" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
server.BUNDLES = ROOT / "pdd-bundles"
server.SKILLS = ROOT / ".reasonix" / "skills"
server.ROOT = ROOT


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
        pytest.skip("no PDD_EVIDENCE_KEY available")
    os.environ["PDD_EVIDENCE_KEY"] = key
    yield


@pytest.fixture(autouse=True)
def _publish_token(monkeypatch):
    monkeypatch.setenv("PDD_PUBLISH_TOKEN", "test-token")
    yield


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    server.PUBLISHED = tmp_path / "published"
    server.DB_PATH = tmp_path / "pdd.db"
    server.EVIDENCE = tmp_path / "evidence"
    yield


@pytest.fixture
def http_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    thread.join()


def _bundle_files(name="demo", namespace="test-ns", version="1.0.0") -> dict:
    return {
        "protocol.yaml": (
            f"protocol:\n  name: {name}\n  version: {version}\n  status: sealed\n"
            f"  namespace: {namespace}\n"),
        "invariants/structural.yaml": "structural_invariants: []\n",
    }


def _submission(files=None, name="demo", namespace="test-ns", version="1.0.0",
                sign_key=None):
    files = files if files is not None else _bundle_files(name, namespace, version)
    bundle_digest = server._bundle_digest_of(files)
    proto = {"name": name, "version": version, "bundle_digest": bundle_digest}
    # pdd.evidence reads the key lazily per call (fail-closed), so swapping
    # the env around build_evidence signs with the override key.
    saved = os.environ.get("PDD_EVIDENCE_KEY")
    if sign_key:
        os.environ["PDD_EVIDENCE_KEY"] = sign_key
    try:
        evidence = pdd_evidence.build_evidence(proto, "sha256:impl", [], [], {})
    finally:
        if sign_key and saved is not None:
            os.environ["PDD_EVIDENCE_KEY"] = saved
    return {
        "namespace": namespace, "name": name, "version": version,
        "bundle": files, "evidence": evidence,
    }


def _post(url, payload, token=None):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url + "/publish", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_publish_requires_bearer_token(http_server):
    status, resp = _post(http_server, _submission())
    assert status == 401
    assert resp["error"]["code"] == "unauthorized"


def test_publish_with_valid_token_reaches_validation(http_server):
    status, resp = _post(http_server, _submission(), token="test-token")
    assert status == 201, resp
    assert resp["status"] == "published"


def test_publish_missing_fields_400():
    status, resp = server._handle_publish({"namespace": "n"})
    assert status == 400
    assert resp["error"]["code"] == "invalid_request"


def test_publish_digest_mismatch_400():
    sub = _submission()
    sub["evidence"]["protocol"]["bundle_digest"] = "sha256:wrong"
    status, resp = server._handle_publish(sub)
    assert status == 400
    assert resp["error"]["code"] == "conflict"


def test_publish_tampered_evidence_400():
    sub = _submission()
    sub["evidence"]["results"] = [{"invariant_id": "B-001", "outcome": "fail"}]
    status, resp = server._handle_publish(sub)
    assert status == 400
    assert "digest mismatch" in resp["error"]["message"]


def test_publish_registry_owned_namespace_requires_valid_hmac():
    # Signed with the WRONG key: digest is internally consistent but the
    # HMAC does not verify -> the registry-owned namespace gate refuses it.
    sub = _submission(namespace="pdd", sign_key="wrong-key")
    status, resp = server._handle_publish(sub)
    assert status == 400
    assert "HMAC" in resp["error"]["message"]


def test_publish_author_namespace_with_wrong_key_is_attested():
    # Author namespaces are honor-system: structurally valid evidence is
    # accepted (attested), the HMAC is not an admission gate.
    sub = _submission(namespace="tactile-taco", sign_key="wrong-key")
    status, resp = server._handle_publish(sub)
    assert status == 201, resp
    assert resp["status"] == "published"


def test_publish_success_writes_evidence_chain():
    sub = _submission()
    status, resp = server._handle_publish(sub)
    assert status == 201, resp
    name = "demo"
    adm = server.EVIDENCE / name / "admission"
    assert len(list(adm.glob("*.evidence.json"))) == 1
    ledger = server.EVIDENCE / name / "runtime-ledger.jsonl"
    assert ledger.exists()
    assert '"attest-pass"' in ledger.read_text()
    assert any(b.get("name") == name for b in server._catalog_strict())
    assert (server.PUBLISHED / name / "protocol.yaml").exists()
    assert (server.PUBLISHED / "test-ns" / name / "1.0.0" / "protocol.yaml").exists()
    with sqlite3.connect(server.DB_PATH) as conn:
        row = conn.execute(
            "SELECT namespace, name, version, status FROM submissions").fetchone()
    assert row == ("test-ns", "demo", "1.0.0", "published")


def test_publish_idempotent_republish():
    sub = _submission()
    assert server._handle_publish(sub)[0] == 201
    status, resp = server._handle_publish(sub)
    assert status == 200
    assert resp["status"] == "already-published"
    ledger = server.EVIDENCE / "demo" / "runtime-ledger.jsonl"
    assert ledger.read_text().count("attest-pass") == 1


def test_publish_ledger_attests_the_on_disk_evidence_file():
    """The ledger's evidence_digest must equal the digest of the admission
    file bytes (the CLI's verify recomputes file digests) — a canonical-json
    digest here would break /evidence/verify attestation."""
    sub = _submission()
    assert server._handle_publish(sub)[0] == 201
    import hashlib
    ledger = server.EVIDENCE / "demo" / "runtime-ledger.jsonl"
    block = json.loads(ledger.read_text().splitlines()[-1])
    obs = block.get("observations") or {}
    adm_file = next((server.EVIDENCE / "demo" / "admission").glob("*.evidence.json"))
    on_disk = "sha256:" + hashlib.sha256(adm_file.read_bytes()).hexdigest()
    assert obs["evidence_digest"] == on_disk
    # and the CLI-style verify (ledger + every admission file) passes
    assert pdd_evidence.verify_ledger(ledger)["ok"] is True
    assert pdd_evidence.verify_evidence_object(adm_file)["ok"] is True


def test_publish_name_collision_with_catalog_409():
    # "user-registry" exists in the git checkout with a different digest.
    sub = _submission(name="user-registry", files=_bundle_files(name="user-registry"))
    status, resp = server._handle_publish(sub)
    assert status == 409
    assert resp["error"]["code"] == "conflict"


def test_publish_new_version_no_collision():
    sub = _submission(name="demo-two", version="1.1.0")
    status, resp = server._handle_publish(sub)
    assert status == 201
    assert resp["version"] == "1.1.0"
