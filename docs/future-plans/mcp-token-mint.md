# Per-agent publish tokens + durable token sync

**Status: partially landed (2026-08-10, MCP Phase B).** Mint/revoke tools
(`registry.admin.token.mint`/`revoke`, admin-gated by `PDD_ADMIN_TOKEN`),
hash-at-rest storage, and the append-only token_audit trail are live in
pdd-registry-mcp 1.1.0; the publish route accepts minted tokens and rejects
revoked ones. Remaining ideas below.

**Problem.** `PDD_PUBLISH_TOKEN` is a single shared credential. Today it is
mirrored into Infisical (see `docs/onboarding.md`) so Infisical members can
publish, but there is no per-agent scoping, rotation story, or audit trail,
and the mirror can go stale if the cluster token is rotated outside push.sh.

**Direction.**
1. **Admin-gated mint tool** — the future registry MCP server exposes
   `registry.token.mint` (admin-only, Bearer-gated): issues a per-agent
   publish token, stores it (Infisical or a cluster Secret / DB table),
   records an audit entry (who, when, scope). The registry's existing
   constant-time Bearer validation (`src/server.py`) accepts it unchanged.
2. **Durable sync** — make the deploy (`deploy/push.sh`) prefer the
   Infisical/env value of `PDD_PUBLISH_TOKEN` and apply it to the cluster
   Secret (create-or-update), so Infisical becomes the source of truth and
   rotations propagate on the next deploy instead of the one-time mirror.
3. **Revocation** — a `registry.token.revoke` tool + a deny-list check in
   the publish path (or token rows in the DB with an `active` flag).

**Why deferred.** Requires the MCP server (mint/revoke surface) and a
deliberate decision on the source of truth (Infisical vs cluster) + rotation
policy. Not needed while the shared token + manual mirror suffice.
