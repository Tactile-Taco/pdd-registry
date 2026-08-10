"""Service tests for the /mcp route (pdd-registry-mcp Phase A, read-only).

Covers the HTTP surface: initialize negotiation, tools/list conformance,
unknown-tool fail-closed (B-001), version tool, skills resources,
submission.check (B-003), registry passthrough with a stubbed handler
(B-002), S-004 stale surface 503, and the body cap.
"""

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pdd_server", ROOT / "src" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
server.ROOT = ROOT
server.BUNDLES = ROOT / "pdd-bundles"
server.EVIDENCE = ROOT / "evidence"
server.SKILLS = ROOT / ".reasonix" / "skills"

import registry_mcp  # noqa: E402
import registry_db  # noqa: E402

EXPECTED_TOOLS = {"registry.version", "registry.search", "registry.index",
                  "registry.evidence.verify", "registry.submission.check",
                  "registry.admin.token.mint", "registry.admin.token.revoke"}


@pytest.fixture()
def mcp_client():
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()


def _post(base, payload, headers=None):
    req = urllib.request.Request(
        base + "/mcp", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


def _bundle(**over):
    b = {"namespace": "acme", "name": "pdd-registry-mcp", "version": "1.1.0",
         "status": "sealed", "digest": "sha256:" + "a" * 64,
         "purpose": "MCP client interface", "tags": ["mcp-server"],
         "depends_on": [], "provides": {},
         "invariants": {"structural": [{"id": "S-001"}]},
         "capabilities": {}, "boundary": {"in_scope": []}}
    b.update(over)
    return b


def _evidence(**over):
    e = {"artifact_id": "pdd-registry-mcp-python-stdlib",
         "resource_identifier": "https://github.com/example/repo/actions/runs/1",
         "decision": "attest-pass",
         "signed_object": {"ok": True},
         "digest": "sha256:" + "c" * 64,
         "bundle_digest": "sha256:" + "a" * 64}
    e.update(over)
    return e


def _publish_evidence(**over):
    """Evidence shaped for the REST publish schema (no bundle_digest —
    additionalProperties:false; submission.check's evidence arg is a
    different contract)."""
    e = {"artifact_id": "pdd-registry-mcp-python-stdlib",
         "resource_identifier": "https://github.com/example/repo/actions/runs/1",
         "decision": "attest-pass", "signed_object": {"ok": True},
         "digest": "sha256:" + "c" * 64}
    e.update(over)
    return e


def test_mcp_initialize_negotiates(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18"}})
    assert status == 200
    assert out["result"]["protocolVersion"] == "2025-06-18"
    assert out["result"]["serverInfo"]["name"] == "pdd-registry-mcp"


def test_mcp_tools_list_matches_contract(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list", "params": {}})
    assert status == 200
    names = [t["name"] for t in out["result"]["tools"]]
    assert names == sorted(EXPECTED_TOOLS)
    assert all(t["description"] and isinstance(t["inputSchema"], dict)
               for t in out["result"]["tools"])


def test_mcp_unknown_tool_fails_closed(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "registry.publish",
                                                "arguments": {}}})
    assert status == 200
    assert out["error"]["code"] == -32602
    assert out["error"]["data"]["kind"] == "not_found"


def test_mcp_version_tool(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "registry.version",
                                                "arguments": {}}})
    assert status == 200
    assert out["result"]["protocol"] == "pdd-registry-mcp"
    assert out["result"]["version"] == "1.0.0"


def test_mcp_skills_resources(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "resources/list", "params": {}})
    assert status == 200
    uris = {r["uri"] for r in out["result"]["resources"]}
    assert "skills://pdd-workflow/latest" in uris
    assert "registry://version" in uris
    status, read = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                      "method": "resources/read",
                                      "params": {"uri": "skills://pdd-workflow/latest"}})
    assert status == 200
    assert "name: pdd-workflow" in read["result"]["contents"][0]["text"]


def test_mcp_version_manifest_resource(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "resources/read",
                                     "params": {"uri": "registry://version"}})
    assert status == 200
    manifest = json.loads(out["result"]["contents"][0]["text"])
    assert manifest["protocol"] == "pdd-registry-mcp"
    assert manifest["version"] == "1.0.0"
    assert manifest["surface_fresh"] is True


def test_mcp_submission_check_pass_and_fail(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "registry.submission.check",
                                                "arguments": {
                                                    "bundle": _bundle(),
                                                    "evidence": _evidence()}}})
    assert status == 200
    assert all(c["pass"] for c in out["result"]["checks"])
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "registry.submission.check",
                                                "arguments": {
                                                    "bundle": _bundle(digest="sha256:bad"),
                                                    "evidence": _evidence(
                                                        resource_identifier="nope",
                                                        bundle_digest="sha256:" + "b" * 64)}}})
    assert status == 200
    failed = [c for c in out["result"]["checks"] if not c["pass"]]
    assert len(failed) >= 3


def test_mcp_search_passthrough_with_stub(mcp_client, monkeypatch):
    def stub_search(args):
        return {"ok": True, "results": [{"name": "user-registry"}]}
    core = registry_mcp.mcp_core.McpCore(search_fn=stub_search)
    monkeypatch.setattr(registry_mcp, "CORE", core)
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/call",
                                     "params": {"name": "registry.search",
                                                "arguments": {"query": "registry"}}})
    assert status == 200
    assert out["result"] == {"ok": True, "results": [{"name": "user-registry"}]}


def test_mcp_surface_stale_returns_503(mcp_client, monkeypatch):
    monkeypatch.setattr(registry_mcp, "CORE", None)
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list", "params": {}})
    assert status == 503
    assert out["error"]["code"] == -32000
    assert "S-004" in out["error"]["message"]


def test_mcp_body_cap(mcp_client):
    status, out = _post(mcp_client, {"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list", "params": {}},
                        headers={"Content-Length": "0"})
    assert status == 400
    assert out["error"]["data"]["kind"] == "invalid_request"


def test_mcp_invalid_json_body(mcp_client):
    req = urllib.request.Request(mcp_client + "/mcp", data=b"{not json",
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            out = json.loads(resp.read().decode())
            assert resp.status == 200  # unreachable: body invalid -> 400
    except urllib.error.HTTPError as err:
        out = json.loads(err.read().decode())
        assert err.code == 400
        assert out["error"]["code"] == -32602


def test_surface_fresh_gate_ok():
    fresh, reason = registry_mcp.surface_fresh()
    assert fresh is True, reason


# --- MCP Phase B: admin token mint/revoke + publish authn -----------------


@pytest.fixture()
def admin_env(monkeypatch, tmp_path):
    """DB-backed server (sqlite :memory:) with admin token set and the
    shared publish token unset, so minted tokens are the only publish
    path."""
    monkeypatch.setattr(server, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(server, "_db_conn", None)
    # registry_mcp is a shared module whose _db_provider may have been
    # bound to ANOTHER test module's server instance (each test module
    # exec's its own copy of server.py) — rebind it to THIS instance.
    monkeypatch.setattr(registry_mcp, "_db_provider", server._db)
    monkeypatch.delenv("PDD_PUBLISH_TOKEN", raising=False)
    monkeypatch.setattr(registry_mcp, "ADMIN_TOKEN", "admin-test-token-123")
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()


def _post_auth(base, payload, token):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + "/mcp", data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


def test_admin_mint_requires_valid_bearer(admin_env):
    status, out = _post_auth(admin_env,
                             {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "registry.admin.token.mint",
                                         "arguments": {"label": "agent-x"}}},
                             token=None)
    assert status == 401
    assert "PDD_ADMIN_TOKEN" in out["error"]["message"]
    status, out = _post_auth(admin_env,
                             {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "registry.admin.token.mint",
                                         "arguments": {"label": "agent-x"}}},
                             token="wrong-token")
    assert status == 401


def test_admin_mint_revoke_and_publish_flow(admin_env):
    # mint (admin bearer) -> plaintext returned once
    status, out = _post_auth(admin_env,
                             {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "registry.admin.token.mint",
                                         "arguments": {"label": "agent-y"}}},
                             token="admin-test-token-123")
    assert status == 200
    minted = out["result"]["token"]
    assert len(minted) == 48
    tid = out["result"]["token_id"]
    # publish with the minted token succeeds (env token is unset)
    status, out = _post_auth(admin_env,
                             {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "registry.submission.check",
                                         "arguments": {"bundle": _bundle(),
                                                        "evidence": _evidence()}}},
                             token=None)
    assert status == 200
    req = urllib.request.Request(
        admin_env + "/publish",
        data=json.dumps({"bundle": _bundle(), "evidence": _publish_evidence()}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {minted}"},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode())
    assert body["ok"] is True
    # revoke -> the same token is rejected
    status, out = _post_auth(admin_env,
                             {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "registry.admin.token.revoke",
                                         "arguments": {"token_id": tid}}},
                             token="admin-test-token-123")
    assert status == 200
    assert out["result"]["revoked"] is True
    req = urllib.request.Request(
        admin_env + "/publish",
        data=json.dumps({"bundle": _bundle(), "evidence": _publish_evidence()}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {minted}"},
        method="POST")
    try:
        urllib.request.urlopen(req)
        raise AssertionError("revoked token must be rejected")
    except urllib.error.HTTPError as err:
        assert err.code == 401


def test_minted_token_stored_hashed_not_plaintext(admin_env):
    status, out = _post_auth(admin_env,
                             {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "registry.admin.token.mint",
                                         "arguments": {"label": "hash-check"}}},
                             token="admin-test-token-123")
    minted = out["result"]["token"]
    rows = registry_db.list_publish_tokens(server._db())
    assert rows and rows[-1]["label"] == "hash-check"
    assert all(minted not in str(r) for r in rows)  # plaintext never at rest
    audit = registry_db.token_audit_rows(server._db())
    assert audit[0]["action"] == "mint"
