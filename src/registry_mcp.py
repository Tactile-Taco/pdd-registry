"""MCP deployment surface for the pdd-registry-mcp protocol (Phase A).

Transport + wiring only: builds the attested pure core (mcp_core.McpCore)
with real handlers for the registry-facing tools (HTTP delegation to the
configured registry URL via a hardened opener), the skills resources
(served from the image's .reasonix/skills), and the S-004 surface-freshness
startup check (keyless staleness gate on the pdd-registry-mcp bundle).

The tool names/descriptions/inputSchemas come from the sealed bundle via
the core's defaults (contract-derived surface); tools/list is validated
against the bundle's tool-registry schema when jsonschema is available.
"""

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = REPO_ROOT / "pdd-bundles" / "pdd-registry-mcp"
SKILLS_DIR = REPO_ROOT / ".reasonix" / "skills"
MCP_CORE_DIR = REPO_ROOT / "implementations" / "pdd-registry-mcp" / "python-stdlib"

# --- S-004 surface freshness: the served surface must derive from the
# --- CURRENT sealed bundle digest (keyless staleness gate on this bundle).
_spec = importlib.util.spec_from_file_location("pdd_cli", REPO_ROOT / "scripts" / "pdd.py")
pdd_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdd_cli)

_spec_core = importlib.util.spec_from_file_location(
    "mcp_core", MCP_CORE_DIR / "mcp_core.py")
mcp_core = importlib.util.module_from_spec(_spec_core)
_spec_core.loader.exec_module(mcp_core)


def surface_fresh() -> tuple[bool, str]:
    """S-004: refuse to serve a surface whose bundle digest drifted from
    the latest admission evidence (dogfoods pdd evidence staleness)."""
    rc = pdd_cli.cmd_evidence_staleness(["pdd-registry-mcp"])
    return (rc == 0, "surface fresh" if rc == 0 else
            "S-004 surface stale: bundle digest differs from the latest "
            "admission — re-run validate + evidence build before serving")


# --- hardened registry fetch (mirrors the CLI's strict opener) -----------

_SCHEME_RE = {"pdd+http", "pdd+https", "http", "https"}


def _registry_get(base_url: str, path: str) -> dict:
    if not base_url.startswith("pdd+http://") and \
            not base_url.startswith("pdd+https://"):
        raise ValueError(f"registry URL must be pdd+http(s)://, got {base_url!r}")
    url = base_url.replace("pdd+", "", 1).rstrip("/") + path
    opener = urllib.request.build_opener()
    # No FileHandler/FTP/Proxy handlers, redirects disabled (a redirect
    # could exfiltrate headers to an attacker-chosen host).
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ARG002
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


# --- handlers --------------------------------------------------------------

def _make_handlers(registry_url: str) -> dict:
    def search_fn(args: dict) -> dict:
        q = args["query"]
        ns = args.get("namespace") or ""
        tag = args.get("tag") or ""
        path = f"/search?q={urllib.request.quote(q)}"
        if ns:
            path += f"&namespace={urllib.request.quote(ns)}"
        if tag:
            path += f"&tag={urllib.request.quote(tag)}"
        return _registry_get(registry_url, path)

    def index_fn(args: dict) -> dict:
        parts = []
        for key in ("status", "namespace", "tag"):
            if args.get(key):
                parts.append(f"{key}={urllib.request.quote(str(args[key]))}")
        return _registry_get(registry_url, "/bundles" + ("?" + "&".join(parts) if parts else ""))

    def evidence_fn(args: dict) -> dict:
        b = args.get("bundle") or ""
        path = "/evidence/verify" + (f"?bundle={urllib.request.quote(b)}" if b else "")
        return _registry_get(registry_url, path)

    return {"search_fn": search_fn, "index_fn": index_fn,
            "evidence_fn": evidence_fn}


def _skill_resources() -> list[dict]:
    """Skills as versioned resources (skills://<name>/latest) + the version
    manifest resource (registry://version). Served from the image."""
    out = []
    if SKILLS_DIR.is_dir():
        for skill_dir in sorted(SKILLS_DIR.glob("pdd-*")):
            md = skill_dir / "SKILL.md"
            if md.is_file():
                out.append({
                    "uri": f"skills://{skill_dir.name}/latest",
                    "name": skill_dir.name,
                    "mimeType": "text/markdown",
                    "description": f"PDD skill: {skill_dir.name} (latest)",
                    "text": md.read_text(encoding="utf-8"),
                })
    proto = (BUNDLE_DIR / "protocol.yaml").read_text(encoding="utf-8") \
        if (BUNDLE_DIR / "protocol.yaml").is_file() else "{}"
    out.append({
        "uri": "registry://version",
        "name": "pdd-registry-mcp version manifest",
        "mimeType": "application/json",
        "description": "Sealed protocol version + skills resource versions",
        "text": json.dumps({
            "protocol": "pdd-registry-mcp",
            "version": mcp_core.PROTOCOL_VERSION,
            "latest_skill_versions": {
                r["uri"].split("//")[1]: "latest" for r in out
                if r["uri"].startswith("skills://")},
            "surface_fresh": surface_fresh()[0],
        }),
    })
    return out


def build_core(registry_url: str):
    """Build the served McpCore or raise SystemExit on S-004 failure."""
    fresh, reason = surface_fresh()
    if not fresh:
        raise SystemExit(f"pdd-registry-mcp S-004: {reason}")
    return mcp_core.McpCore(
        tools=mcp_core.DEFAULT_TOOL_DEFS,
        resources=_skill_resources(),
        **_make_handlers(registry_url),
    )


# Module-level shared instance (the container sets PDD_REGISTRY_URL; the
# filesystem-mode fallback keeps local/dev behaviour working).
REGISTRY_URL = (REPO_ROOT / ".mcp-registry-url").read_text().strip() \
    if (REPO_ROOT / ".mcp-registry-url").is_file() else ""
import os  # noqa: E402
REGISTRY_URL = os.environ.get("PDD_REGISTRY_URL", REGISTRY_URL)

if REGISTRY_URL:
    try:
        CORE = build_core(REGISTRY_URL)
    except SystemExit:
        CORE = None  # S-004 failure: the /mcp route reports it; do not die
else:
    CORE = mcp_core.McpCore(resources=_skill_resources())


def handle_request(body: bytes) -> tuple[dict, int]:
    """Serve one HTTP JSON-RPC request (read-only Phase A surface)."""
    if CORE is None:
        return ({"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32000, "message": "S-004 surface stale",
                           "data": {"kind": "internal"}}}), 503
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ({"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32602, "message": "invalid JSON body",
                           "data": {"kind": "invalid_request"}}}), 400
    return CORE.handle(request), 200
