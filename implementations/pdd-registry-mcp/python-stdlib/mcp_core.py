"""Pure JSON-RPC 2.0 dispatch core for the pdd-registry-mcp protocol.

This is the ATTESTED CANDIDATE (stdlib only, no IO): the caller supplies
everything it needs — tool implementations for the registry-facing tools
(registry.search/index/evidence.verify) and the resources list. The
transport (stdio, streamable HTTP) and the registry HTTP client are
deployment surface (src/registry_mcp.py), never this module.

Invariants implemented here:
  S-001 surface conformance: tools/list + resources/list come from the
       caller-supplied registries (the deployment surface builds them from
       the sealed bundle schemas);
  S-002 error envelope: every failure returns {code, message, data.kind}
       with kinds from {invalid_request, not_found, internal};
  B-001 fail closed: unknown tool/URI/method -> error envelope, no state;
  B-002 passthrough: search/index return the injected function's results
       unchanged;
  B-003 submission checks: structural pre-push checks on caller-supplied
       bundle/evidence contents (never claims the validator loop ran).
"""

import json  # noqa: F401  (used by callers; kept for parity with stdlib-only scan)
import re

# S-007 (pdd-registry) resource_identifier format, mirrored: http(s) URL or
# urn: URN, full string capped at 2048 chars (lookahead-anchored).
RESOURCE_ID_RE = re.compile(
    r"^(?=.{1,2048}$)(https?://|urn:)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_TAGS = 8

DEFAULT_TOOL_DEFS = [
    {"name": "registry.version",
     "description": "Report the served protocol version and tool set (S-003/S-004).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                      "properties": {}}},
    {"name": "registry.search",
     "description": "Ranked keyword search over the registry catalog; delegates to the registry API and returns its results unchanged (B-002).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                      "required": ["query"],
                      "properties": {"query": {"type": "string", "minLength": 1},
                                     "namespace": {"type": "string"},
                                     "tag": {"type": "string"}}}},
    {"name": "registry.index",
     "description": "Filtered catalog listing (status/namespace/tag filters, stable order); delegates to the registry API (B-002).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                      "properties": {"status": {"type": "string"},
                                     "namespace": {"type": "string"},
                                     "tag": {"type": "string"}}}},
    {"name": "registry.evidence.verify",
     "description": "Verify evidence records stored by the registry (S-007 honor system: presence, resource_identifier, decision, signature; three-state verified/unverified/attested after the attestation change).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                      "properties": {"bundle": {"type": "string"},
                                     "namespace": {"type": "string"}}}},
    {"name": "registry.submission.check",
     "description": "Structural pre-push checks on caller-supplied bundle/evidence contents: shape, S-007 resource_identifier format, digest consistency (B-003). Never claims the validator loop ran (honor system).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                      "required": ["bundle", "evidence"],
                      "properties": {"bundle": {"type": "object"},
                                     "evidence": {"type": "object"}}}},
]

DEFAULT_TOOL_NAMES = {t["name"] for t in DEFAULT_TOOL_DEFS}
PROTOCOL_VERSION = "1.0.0"
MCP_PROTOCOL_VERSION = "2025-06-18"


class McpCore:
    """Stateless JSON-RPC dispatcher. Thread-safe: no mutable state."""

    def __init__(self, tools=None, search_fn=None, index_fn=None,
                 evidence_fn=None, resources=None, protocol_version=None):
        self._tools = list(tools) if tools is not None else list(DEFAULT_TOOL_DEFS)
        self._tool_names = {t["name"] for t in self._tools}
        self._search_fn = search_fn
        self._index_fn = index_fn
        self._evidence_fn = evidence_fn
        self._resources = list(resources or [])
        self._protocol_version = protocol_version or PROTOCOL_VERSION

    # -- dispatch ---------------------------------------------------------

    def handle(self, request):
        """Dispatch one JSON-RPC request dict; returns the response dict."""
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error(None, -32602, "invalid request envelope",
                               "invalid_request")
        rid = request.get("id")
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return self._error(rid, -32602, "method must be a non-empty "
                                            "string", "invalid_request")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return self._error(rid, -32602, "params must be an object",
                               "invalid_request")
        handler = getattr(self, "_m_" + method.replace("/", "_"), None)
        if handler is None:
            return self._error(rid, -32601, f"method not found: {method}",
                               "not_found")
        try:
            return handler(rid, params)
        except Exception as exc:  # noqa: BLE001 — S-002 envelope, no internals
            return self._error(rid, -32000, "internal error", "internal",
                               detail=str(exc)[:200])

    # -- MCP methods ------------------------------------------------------

    def _m_initialize(self, rid, params):  # noqa: ARG002 — protocol method
        return self._ok(rid, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "pdd-registry-mcp",
                           "version": self._protocol_version},
        })

    def _m_tools_list(self, rid, params):  # noqa: ARG002
        return self._ok(rid, {"tools": sorted(self._tools,
                                              key=lambda t: t["name"])})

    def _m_tools_call(self, rid, params):
        name = params.get("name")
        if name not in self._tool_names:
            return self._error(rid, -32602, f"unknown tool {name!r}",
                               "not_found")  # B-001 fail closed
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return self._error(rid, -32602, "arguments must be an object",
                               "invalid_request")
        return self._call_tool(rid, name, args)

    def _m_resources_list(self, rid, params):  # noqa: ARG002
        return self._ok(rid, {"resources": [
            {"uri": r["uri"], "name": r.get("name", r["uri"]),
             "mimeType": r.get("mimeType", "text/markdown"),
             "description": r.get("description", "")}
            for r in self._resources]})

    def _m_resources_read(self, rid, params):
        uri = params.get("uri")
        for r in self._resources:
            if r.get("uri") == uri:
                return self._ok(rid, {"contents": [{
                    "uri": uri,
                    "mimeType": r.get("mimeType", "text/markdown"),
                    "text": r.get("text", "")}]})
        return self._error(rid, -32602, f"unknown resource {uri!r}",
                           "not_found")  # B-001 fail closed

    # -- tools ------------------------------------------------------------

    def _call_tool(self, rid, name, args):
        if name == "registry.version":
            return self._ok(rid, {"protocol": "pdd-registry-mcp",
                                  "version": self._protocol_version,
                                  "tools": sorted(self._tool_names)})
        if name == "registry.search":
            return self._registry_tool(rid, args, self._search_fn,
                                       required=("query",), passthrough=True)
        if name == "registry.index":
            return self._registry_tool(rid, args, self._index_fn,
                                       required=(), passthrough=True)
        if name == "registry.evidence.verify":
            return self._registry_tool(rid, args, self._evidence_fn,
                                       required=(), passthrough=True)
        if name == "registry.submission.check":
            return self._ok(rid, {"checks": self._submission_checks(args)})
        return self._error(rid, -32602, f"unhandled tool {name!r}", "internal")

    def _registry_tool(self, rid, args, fn, required, passthrough):
        if fn is None:
            return self._error(rid, -32000, "registry not configured",
                               "internal")
        for key in required:
            if not args.get(key):
                return self._error(rid, -32602,
                                   f"missing required argument: {key}",
                                   "invalid_request")
        try:
            result = fn(dict(args))
        except Exception as exc:  # noqa: BLE001 — S-002 envelope
            return self._error(rid, -32000, "registry call failed",
                               "internal", detail=str(exc)[:200])
        # B-002: passthrough — the registry's result is returned unchanged.
        return self._ok(rid, result) if passthrough else result

    # -- B-003 submission checks (structural, honor-system honest) --------

    def _submission_checks(self, args):
        """Structural pre-push checks on caller-supplied contents.

        bundle: {namespace, name, version, digest, tags, ...}
        evidence: {resource_identifier, decision, bundle_digest, ...}
        Never claims the validator loop ran (S-007 honor system)."""
        checks = []
        bundle = args.get("bundle")
        evidence = args.get("evidence")

        def add(name_, ok_, reason_):
            checks.append({"check": name_, "pass": bool(ok_),
                           "reason": None if ok_ else reason_})

        if not isinstance(bundle, dict) or not isinstance(evidence, dict):
            add("shape", False, "bundle and evidence must be objects")
            return checks
        ns = bundle.get("namespace")
        name = bundle.get("name")
        add("bundle.shape",
            isinstance(ns, str) and KEBAB_RE.fullmatch(ns or "")
            and isinstance(name, str) and name and
            KEBAB_RE.fullmatch(name or ""),
            "namespace/name must be kebab-case")
        tags = bundle.get("tags") or []
        add("bundle.tags",
            isinstance(tags, list) and 0 < len(tags) <= MAX_TAGS
            and len(set(tags)) == len(tags)
            and all(isinstance(t, str) and KEBAB_RE.fullmatch(t) for t in tags),
            "tags: 1..8 unique kebab-case")
        digest = bundle.get("digest")
        add("bundle.digest", isinstance(digest, str)
            and bool(SHA256_RE.fullmatch(digest or "")),
            "digest must be sha256:<64 hex>")
        rid = evidence.get("resource_identifier")
        add("evidence.resource_identifier",
            isinstance(rid, str) and bool(RESOURCE_ID_RE.fullmatch(rid)),
            "resource_identifier must be an http(s) URL or urn: URN (S-007)")
        add("evidence.decision",
            evidence.get("decision") == "attest-pass",
            "decision must be attest-pass")
        attested = evidence.get("bundle_digest")
        add("evidence.freshness",
            isinstance(attested, str) and attested == digest,
            "evidence bundle_digest must equal the submitted bundle digest")
        return checks

    # -- envelope helpers -------------------------------------------------

    @staticmethod
    def _ok(rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _error(rid, code, message, kind, detail=None):
        data = {"kind": kind}
        if detail:
            data["detail"] = detail
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": code, "message": message, "data": data}}
