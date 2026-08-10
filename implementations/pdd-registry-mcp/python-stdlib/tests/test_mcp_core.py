"""Contract tests for the pdd-registry-mcp attested core (stdlib only)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_core import McpCore, RESOURCE_ID_RE  # noqa: E402


def _req(method, params=None, rid=1):
    r = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        r["params"] = params
    return r


def _bundle(**over):
    b = {"namespace": "pdd", "name": "pdd-registry-mcp", "version": "1.0.0",
         "digest": "sha256:" + "a" * 64, "tags": ["mcp-server", "registry-client"]}
    b.update(over)
    return b


def _evidence(**over):
    e = {"resource_identifier": "https://github.com/example/repo/actions/runs/1",
         "decision": "attest-pass",
         "bundle_digest": "sha256:" + "a" * 64}
    e.update(over)
    return e


def test_S001_tools_list_matches_contract():
    core = McpCore()
    out = core.handle(_req("tools/list"))
    assert "error" not in out
    names = [t["name"] for t in out["result"]["tools"]]
    assert names == sorted({"registry.version", "registry.search",
                            "registry.index", "registry.evidence.verify",
                            "registry.submission.check",
                            "registry.admin.token.mint",
                            "registry.admin.token.revoke"})
    for t in out["result"]["tools"]:
        assert t["description"] and isinstance(t["inputSchema"], dict)


def test_S002_error_envelope_shape():
    core = McpCore()
    for bad in ("nonsense", 42, {"jsonrpc": "1.0", "id": 1, "method": "x"}):
        out = core.handle(bad)
        err = out.get("error")
        assert err is not None and err["code"] in (-32602, -32601, -32000)
        assert err["data"]["kind"] in ("invalid_request", "not_found",
                                       "internal")
        assert err["message"]


def test_B004_admin_mint_dispatch_passthrough():
    captured = {}
    def mint_fn(args):
        captured.update(args)
        return {"token_id": 7, "token": "t" * 48, "label": args["label"],
                "active": True}
    core = McpCore(mint_fn=mint_fn)
    out = core.handle(_req("tools/call", {"name": "registry.admin.token.mint",
                                          "arguments": {"label": "agent-1"}}))
    assert out["result"]["token_id"] == 7
    assert captured["label"] == "agent-1"


def test_B004_admin_mint_requires_label():
    core = McpCore(mint_fn=lambda a: {"ok": True})
    out = core.handle(_req("tools/call", {"name": "registry.admin.token.mint",
                                          "arguments": {}}))
    assert out["error"]["data"]["kind"] == "invalid_request"


def test_B005_admin_revoke_dispatch_passthrough():
    captured = {}
    def revoke_fn(args):
        captured.update(args)
        return {"revoked": True}
    core = McpCore(revoke_fn=revoke_fn)
    out = core.handle(_req("tools/call", {"name": "registry.admin.token.revoke",
                                          "arguments": {"token_id": 7}}))
    assert out["result"]["revoked"] is True
    assert captured["token_id"] == 7


def test_B005_admin_unconfigured_store_fails_closed():
    core = McpCore()  # no revoke_fn
    out = core.handle(_req("tools/call", {"name": "registry.admin.token.revoke",
                                          "arguments": {"token_id": 7}}))
    assert out["error"]["data"]["kind"] == "internal"


def test_B001_unknown_tool_fails_closed():
    core = McpCore()
    out = core.handle(_req("tools/call", {"name": "registry.publish",
                                          "arguments": {}}))
    assert out["error"]["code"] == -32602
    assert out["error"]["data"]["kind"] == "not_found"


def test_B001_unknown_resource_fails_closed():
    core = McpCore(resources=[{"uri": "skills://pdd-workflow/latest"}])
    out = core.handle(_req("resources/read", {"uri": "skills://nope/latest"}))
    assert out["error"]["data"]["kind"] == "not_found"


def test_B001_unknown_method_fails_closed():
    core = McpCore()
    out = core.handle(_req("prompts/list"))
    assert out["error"]["data"]["kind"] == "not_found"


def test_initialize_negotiates_protocol_version():
    core = McpCore()
    out = core.handle(_req("initialize", {"protocolVersion": "2025-06-18"}))
    assert out["result"]["protocolVersion"] == "2025-06-18"
    assert out["result"]["serverInfo"]["name"] == "pdd-registry-mcp"


def test_B002_search_passthrough_unchanged():
    captured = {}
    def search_fn(args):
        captured.update(args)
        return {"ok": True, "results": [{"name": "user-registry"}]}
    core = McpCore(search_fn=search_fn)
    out = core.handle(_req("tools/call", {"name": "registry.search",
                                          "arguments": {"query": "registry"}}))
    assert out["result"] == {"ok": True, "results": [{"name": "user-registry"}]}
    assert captured["query"] == "registry"


def test_B002_search_requires_query():
    core = McpCore(search_fn=lambda args: {"ok": True})
    out = core.handle(_req("tools/call", {"name": "registry.search",
                                          "arguments": {}}))
    assert out["error"]["data"]["kind"] == "invalid_request"


def test_B002_unconfigured_registry_fails_closed():
    core = McpCore()  # no search_fn injected
    out = core.handle(_req("tools/call", {"name": "registry.search",
                                          "arguments": {"query": "x"}}))
    assert out["error"]["data"]["kind"] == "internal"


def test_B003_submission_checks_pass():
    core = McpCore()
    out = core.handle(_req("tools/call", {"name": "registry.submission.check",
                                          "arguments": {
                                              "bundle": _bundle(),
                                              "evidence": _evidence()}}))
    checks = out["result"]["checks"]
    assert checks and all(c["pass"] for c in checks)


def test_B003_submission_checks_report_reasons():
    core = McpCore()
    out = core.handle(_req("tools/call", {"name": "registry.submission.check",
                                          "arguments": {
                                              "bundle": _bundle(digest="sha256:bad"),
                                              "evidence": _evidence(
                                                  resource_identifier="not-a-url",
                                                  decision="attest-fail",
                                                  bundle_digest="sha256:" + "b" * 64)}}))
    by_name = {c["check"]: c for c in out["result"]["checks"]}
    assert not by_name["bundle.digest"]["pass"]
    assert "sha256:<64 hex>" in by_name["bundle.digest"]["reason"]
    assert not by_name["evidence.resource_identifier"]["pass"]
    assert not by_name["evidence.decision"]["pass"]
    assert not by_name["evidence.freshness"]["pass"]


def test_B003_submission_checks_never_claim_validation():
    """The checks are structural; the result must not contain a verdict."""
    core = McpCore()
    out = core.handle(_req("tools/call", {"name": "registry.submission.check",
                                          "arguments": {
                                              "bundle": _bundle(),
                                              "evidence": _evidence()}}))
    blob = json.dumps(out)
    assert "verdict" not in blob and "admit" not in blob


def test_S003_version_tool_reports_protocol():
    core = McpCore()
    out = core.handle(_req("tools/call", {"name": "registry.version",
                                          "arguments": {}}))
    assert out["result"]["protocol"] == "pdd-registry-mcp"
    assert out["result"]["version"] == "1.0.0"


def test_resources_list_and_read():
    core = McpCore(resources=[
        {"uri": "skills://pdd-workflow/latest", "name": "pdd-workflow",
         "mimeType": "text/markdown", "text": "# pdd-workflow\n",
         "description": "workflow skill"}])
    listed = core.handle(_req("resources/list"))
    assert listed["result"]["resources"][0]["uri"] == "skills://pdd-workflow/latest"
    read = core.handle(_req("resources/read", {"uri": "skills://pdd-workflow/latest"}))
    assert read["result"]["contents"][0]["text"] == "# pdd-workflow\n"


def test_resource_id_re_caps_full_string_at_2048():
    assert RESOURCE_ID_RE.fullmatch("https://x/" + "a" * 2038)
    assert not RESOURCE_ID_RE.fullmatch("https://x/" + "a" * 2048)  # 2056 total
