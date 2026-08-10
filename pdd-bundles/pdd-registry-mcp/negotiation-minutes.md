# Negotiation Minutes — pdd-registry-mcp

## v1.0.0 version event (MCP client-interface protocol, Phase A)

- **Decision (2026-08-09, user-confirmed design)**: agents interact with
  the DB-backed registry through an MCP server whose surface is generated
  from a sealed protocol bundle — killing the skill/registry version-drift
  problem by construction. The user confirmed: stdlib server, stdio +
  streamable-HTTP (`/mcp` route in the same container), all PDD skills
  offered as versioned resources (client opt-in), own sealed bundle as the
  first `mcp-server`-taxonomy entry, S-008-style startup check, phased
  authz (Phase A read-only; Phase B admin token).
- **Version**: 1.0.0 (first sealed version of a new protocol; additive
  surface by S-003 thereafter).
- **Invariants**: S-001 surface conformance (tool/resource schemas),
  S-002 error envelope, S-003 handshake versioning, S-004 surface
  freshness (staleness gate on this bundle); B-001 fail-closed unknown
  tool/URI, B-002 registry passthrough, B-003 submission checks (never
  claims validation); O-001..O-005 (stdlib-only, no writes/network/
  background work in the attested core).
- **Compatibility matrix**: pdd-registry-mcp 1.0.0 (sealed), depends_on
  pdd-registry (any 1.x); provides pdd-registry-mcp.tools/.resources/
  .errors; no dependents; no conflicts. Tag vocabulary addition:
  `mcp-server` (deliberate, documented in ambiguity log), `registry-client`
  (existing seed).
- **Enforcement**: contract tests on the attested core (JSON-RPC dispatch +
  tool semantics), mutation sanity on B-001, staleness gate (S-004) in CI +
  at server startup, bundle lint.
- **Known limitations (candidate manifest)**: transport (stdio/HTTP) is
  deployment surface; registry fetch is caller-injected for the core;
  publish is not an MCP tool in Phase A; skills resources are served from
  the image's .reasonix/skills (pdd-* only).
