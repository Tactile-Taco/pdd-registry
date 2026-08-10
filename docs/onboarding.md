# Access & Onboarding — tokens, keys, and who can read what

This is the inventory of every credential a human or agent needs to interact
with the pdd registry (the DB-backed registry on the M6 staging cluster) and
its evidence chains. It is also the onboarding path: if you are a new agent
or user, start at [How to get access](#how-to-get-access).

> **Known gap (documented 2026-08-09):** there is no *formal* request/grant
> process for membership — access currently flows through Infisical project
> membership, which is granted by the operator. The publish token was
> previously cluster-only; it has now been **mirrored into Infisical**
> (misc-secrets, prod) so Infisical members can publish. Per-agent minting
> is a future plan (see `docs/future-plans/mcp-token-mint.md`).

## Token inventory

| Token / secret | Purpose | Where it lives | Who can read | How to acquire |
|---|---|---|---|---|
| `PDD_PUBLISH_TOKEN` | Bearer auth for `POST /publish` (registry ingest) | 1) k8s Secret `pdd-publish-token` (staging cluster) — source of truth; 2) **Infisical misc-secrets (`5598630f-…`), env `prod`** (mirrored 2026-08-09, verified byte-identical) | registry pod; deploy runner (ssh+sudo); Infisical members (since the mirror) | `infisical secrets get PDD_PUBLISH_TOKEN --projectId 5598630f-4109-47d9-bbfb-91bac16ac92c --env prod --plain` |
| `PDD_ADMIN_TOKEN` | Bearer auth for the MCP admin tools (`registry.admin.token.mint`/`revoke`, Phase B) | 1) k8s Secret `pdd-admin-token` (staging cluster, created idempotently by push.sh); 2) **Infisical misc-secrets, env `prod`** (mirrored 2026-08-10) | registry pod; deploy runner; Infisical members | `infisical secrets get PDD_ADMIN_TOKEN --projectId 5598630f-4109-47d9-bbfb-91bac16ac92c --env prod --plain` — use only to mint per-agent tokens via the MCP admin tools (minted tokens are stored hashed in the DB and can be revoked individually) |
| `PDD_EVIDENCE_KEY` | HMAC-SHA256 signing/verifying of admission evidence objects | Infisical nixos-infra (`7a2f10fc-…`), env `prod`; GitHub Actions secret; k8s Secret `pdd-evidence-key` | Infisical members of that project; GitHub repo admins | `infisical secrets get PDD_EVIDENCE_KEY --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain` |
| `GHCR_PAT` | Push the OCI image to GitHub Container Registry | Infisical nixos-infra, env `prod` | Infisical members; deploy runner | `infisical secrets get GHCR_PAT --projectId 7a2f10fc-… --env prod` |
| `SOPS_AGE_KEY` | Decrypt sops-encrypted infra files (nixos-infra) | Infisical nixos-infra, env `prod` | Infisical members | via Infisical |
| `STAGING_TAILSCALE_IP` / `STAGING_TAILSCALE_DNS` | Staging guest address (tailnet) | Infisical misc-secrets, env `prod` | Infisical members | via Infisical |
| `STAGING_SSH_KEY` | ssh to the staging guest (deploy) | GitHub Actions secret only | GitHub repo admins / CI | not in Infisical (deliberate; could be mirrored on request) |
| `POSTGRES_PASSWORD` / `PDD_DATABASE_URL` | Registry database | k8s Secret `pdd-postgres` (cluster-only) | pod + deploy runner | never leaves the cluster by design |
| `STAGING_KNOWN_HOSTS` | Public ed25519 host key of the guest (ssh pinning) | committed `deploy/staging-known_hosts` | public | not secret |

## How to get access

1. **Infisical membership** (the one prerequisite): the operator adds you to
   the `nixos-infra` (`7a2f10fc-…`) and `misc-secrets` (`5598630f-…`)
   projects. There is currently no self-service request flow — ask the
   operator. *(Future: see `docs/future-plans/mcp-token-mint.md` for
   per-agent minting.)*
2. **Read the keys you need** (commands above — never print them into chat
   or commit them).
3. **Verify you can talk to the registry**:
   ```bash
   export PDD_EVIDENCE_KEY=$(infisical secrets get PDD_EVIDENCE_KEY --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain --silent)
   export PDD_PUBLISH_TOKEN=$(infisical secrets get PDD_PUBLISH_TOKEN --projectId 5598630f-4109-47d9-bbfb-91bac16ac92c --env prod --plain --silent)
   # evidence chain verifies (author-side, no registry needed):
   pdd evidence verify pdd-registry
   # registry reachable + records verify (honor system, S-007):
   pdd evidence verify pdd-registry --registry pdd+https://pdd-repository.<STAGING_TAILSCALE_DNS>/
   ```
4. **Publishing** (if you author protocols): `pdd publish <bundle-dir>
   --evidence <file> --registry pdd+https://pdd-repository.<dns>/` — the CLI
   sends `PDD_PUBLISH_TOKEN` automatically when set.

## Rules

- Never write tokens/keys into files, chat, commits, or evidence artifacts
  (the evidence greps for `token|password|private-key` patterns).
- `PDD_EVIDENCE_KEY` is required by `make test` / `make evidence`;
  `PDD_PUBLISH_TOKEN` is required only for publishing.
- A mirrored value may go stale if the cluster token is ever rotated outside
  push.sh — re-run the mirror (read cluster → set Infisical) or implement
  the durable sync in `docs/future-plans/mcp-token-mint.md`.
