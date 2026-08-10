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

## v1.1.0 version event (admin token mint/revoke, MCP Phase B)

- **Decision (2026-08-10)**: the phased authz plan's Phase B lands: a
  separate PDD_ADMIN_TOKEN (cluster Secret `pdd-admin-token`, created
  idempotently by push.sh, mirrored into Infisical misc-secrets) gates the
  new `registry.admin.token.mint` / `registry.admin.token.revoke` tools;
  per-agent publish tokens are stored HASHED in the DB (plaintext returned
  once), every mint/revoke appends to the token_audit trail, and the
  publish route accepts either the shared env token or an ACTIVE minted
  token (revoked tokens are rejected).
- **Version**: 1.1.0 minor (S-003: optional ADDITIONS only — two new tools
  + two new invariants; nothing removed/renamed/made-required).
- **Invariants**: B-004 (mint: once-only plaintext, hash at rest, audit,
  admin bearer), B-005 (revoke: deactivates, audit, never re-activates).
- **Enforcement**: admin bearer at the /mcp route (constant-time compare,
  TypeError->401), registry_db mint/revoke/verify (parameterized,
  serialized, audited), contract tests at both layers (candidate dispatch
  + service flows), publish-route acceptance of minted tokens.
- **Compatibility matrix**: pdd-registry-mcp 1.1.0 (1.0.0 in git history),
  sealed; same depends_on (pdd-registry); no dependents; additive surface.
