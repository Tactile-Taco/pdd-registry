---
name: pdd-registry-client
description: Add, search, pull and query the pdd protocol registry (pdd-bundles/ + v2 HTTP API): bundle admission, pdd index/search, /search and /bundles views.
---

# pdd-registry-client

Operate the **pdd protocol registry** of `Tactile-Taco/pdd-repository`: admit
new protocol bundles, search the catalog (CLI and HTTP), and pull/inspect
bundles, invariants, capabilities, and evidence ledgers. The registry is
read-only over HTTP; **git is the distribution layer** (no push/pull over
HTTP — a documented decision, see docs/service-features-v2.md).

## Layout (what the registry contains)

| Path | Role |
|---|---|
| `pdd-bundles/<name>/` | the registry: sealed protocol bundles (protocol.yaml, invariants/{S,B,O}.yaml, capability-manifest.yaml, evidence-requirements.yaml, validators/, ambiguity-log.md, negotiation-minutes.md) |
| `implementations/<name>/<variant>/` | candidate realizations (candidate-manifest.json + tests with invariant lineage) |
| `evidence/<name>/` | signed admission evidence, discovery logs, validation results, `runtime-ledger.jsonl` |
| `scripts/pdd.py` | the CLI (`bundle lint/seal`, `validate`, `evidence build/verify`, `run`, `index`, `search`) |
| `src/server.py` + `src/registry_index.py` | the HTTP service; shares the SAME index as the CLI |

The catalog is built **live** from `pdd-bundles/*` — adding a directory is
the whole "registration". No auth anywhere (tailnet-only); everything is
read-only except git+deploy.

## ADD — admit a new protocol bundle

```bash
cd <repo>  # pdd-repository worktree
mkdir pdd-bundles/my-protocol
cp -r .reasonix/skills/pdd-protocol-author/assets/templates/* pdd-bundles/my-protocol/
# Author: protocol.yaml (name/version/status + purpose, boundary, depends_on,
# provides), S/B/O invariants, capability manifest, ambiguity log.
make lint                                        # check_bundle.py must pass (gate)
python3 scripts/pdd.py bundle seal my-protocol   # status: draft → sealed

# Implementation (candidate, never authoritative):
mkdir -p implementations/my-protocol/<variant>/tests
# candidate-manifest.json + tests that cite invariant ids (B-001, S-002, …)

# Three-layer validation (S schema/conformance, B property+mutation, O allowlist/sandbox):
python3 scripts/pdd.py validate my-protocol --impl implementations/my-protocol/<variant>

# Evidence (needs the signing key — Infisical, nixos-infra project):
export PDD_EVIDENCE_KEY=$(infisical secrets get PDD_EVIDENCE_KEY \
  --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain --silent)
python3 scripts/pdd.py evidence build my-protocol --impl implementations/my-protocol/<variant>
python3 scripts/pdd.py evidence verify my-protocol
```

- The Makefile targets `validate`/`evidence` are hardcoded to `user-registry`;
  for other bundles use `scripts/pdd.py` directly (above).
- `make all` = lint+test+validate+evidence (the commit gate; needs the key).
- After commit+push to `main`, CI runs `pdd-validator-loop` (hosted) and
  `pdd-staging-deploy` (self-hosted runner `m6-pdd`, label `staging-deploy`)
  automatically.

## SEARCH — the catalog

CLI (same index as the HTTP API; requires pyyaml, fail-closed without it):

```bash
python3 scripts/pdd.py index                # JSON catalog: versions, statuses,
                                            # depends_on, provides, invariant
                                            # counts, capability keys
python3 scripts/pdd.py search <query>       # ranked matches {bundle, layer, id,
                                            # text, score}; exit 0 if ≥1 match
```

HTTP (service at https://pdd-repository.<STAGING_TAILSCALE_DNS>; tailnet DNS
does not resolve from shells — always pin the address):

```bash
curl -sk --resolve pdd-repository.$STAGING_TAILSCALE_DNS:443:$STAGING_TAILSCALE_IP \
  "https://pdd-repository.$STAGING_TAILSCALE_DNS/search?q=idempotent"
```

Search semantics (docs/service-features-v2.md): tokens are ANDed **per entry**
(name, purpose, invariant id+statement, capability keys); ranked by field
weight (name 10 > purpose 5 > invariant 3 > capability 2), stable tiebreak.
`idempotent network` finds nothing even if both words appear somewhere.

## PULL / INSPECT — fetch and view bundles

```bash
git pull origin main          # git IS the distribution layer
python3 scripts/pdd.py index  # refresh the local catalog
```

HTTP views (all read-only, 404 for unknown bundles):

| Endpoint | Returns |
|---|---|
| `/bundles?status=sealed&depends_on=X` | filtered index `{name, version, status, depends_on, provides}` |
| `/bundles/{name}` | summary + `invariant_ids` per layer |
| `/bundles/{name}/invariants?severity=must` | full S/B/O invariant items (severity filter: must/should) |
| `/bundles/{name}/capabilities` | capability manifest (network, filesystem, secrets, …) |
| `/bundles/{name}/ledger?limit=N` | last N ledger blocks + `verified` (real HMAC-chain check; no key → `verified:false`) |
| `/evidence/verify`, `/evidence/admission` | per-bundle ledger verification; admitted digests |

Same `curl --resolve` pattern as SEARCH; `?limit=0` → zero blocks, negative/
non-integer limit → 400.

## Gotchas

- **Never put `PDD_EVIDENCE_KEY` in argv/echo** — pipe via stdin
  (`printf '%s' "$KEY" | … --password-stdin` / `--from-env-file=/dev/stdin`).
- **`make validate`/`make all` on a docker-less machine** rewrites
  `evidence/*/validation/*.results.json` with O-001/O-002 → `skip`, diverging
  from the attested sandbox-pass version; restore with `git checkout -- evidence/`.
- **pyyaml is required** for index/search/v2 views (pinned in the Dockerfile);
  without it the CLI/API fail closed with an explicit error.
- **The registry is public** — never put machine addresses or secrets in
  bundles, commits, or docs; fetch addresses from Infisical at use time.
- Registry service deploys via `deploy/push.sh` (docker build → ghcr → k3s
  on the staging guest); image digest is pinned in `deploy/k8s.yaml`; see
  docs/handoff.md for the runner/CI state.
