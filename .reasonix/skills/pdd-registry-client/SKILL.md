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
| `evidence/<name>/` | signed admission evidence + discovery logs (stem-keyed `{impl[:16]}-{bundle[:12]}` since v1.1), validation results, `runtime-ledger.jsonl` |
| `scripts/pdd.py` | the CLI (`bundle lint/seal`, `validate`, `evidence build/verify/latest/staleness`, `run`, `index`, `search`, `publish`) |
| `src/server.py` + `src/registry_index.py` + `src/registry_db.py` | the HTTP service; shares the SAME index as the CLI; `registry_db.py` is the v1.2 database adapter (PostgreSQL in prod, sqlite for dev/tests) |

The catalog is built **live** from `pdd-bundles/*` — adding a directory is
the whole "registration". Since the v1.1 version event every bundle declares
`namespace` + `tags`; the display address is `namespace/name` (directories
and evidence stay name-keyed). Reads are unauthenticated (tailnet-only);
the only write surface is the v1.2 publish handshake, which requires the
bearer token (see below).

Since **v1.2 the registry is DB-backed** (PostgreSQL in staging k3s on the
M6 mini-pc, `deploy/postgres.yaml`; pdd-registry protocol 1.2.0, S-006):

- **Same commands, new resource identifier**: `pdd index`, `pdd search`,
  `pdd evidence verify` accept `--registry pdd+http(s)://<host>/` (or
  `$PDD_REGISTRY`) and talk to the DB-backed registry server — the
  resource is the registry URL, not the filesystem.
- **Publishing**: `pdd publish <bundle-dir> --evidence <file>
  --registry pdd+http(s)://…` submits a bundle + its signed evidence
  (idempotent by (namespace, name, version, digest), B-006). Publishes are
  authenticated with a bearer token: the CLI sends `PDD_PUBLISH_TOKEN` when
  set (the registry pod + deploy runner have it from the pdd-publish-token
  Secret); unauthenticated publishes are rejected 401. The token travels in
  the header — use `pdd+https://` for remote registries, `pdd+http://` only
  for localhost/tailnet-LAN (the in-cluster seed uses localhost). The registry does
  NOT own a git repo of protocols; the author-side git chain stays the
  source of truth and `deploy/push.sh` seeds the DB on deploy
  (via `pdd evidence latest <name>`).
- **Author-owned validation (honor system)**: the registry does not run
  the validator loop and does not prove validation. Every evidence record
  must carry a `resource_identifier` (S-007) — an http(s) URL or `urn:`
  pointing at the author's validator-loop execution record (e.g. a CI/CD
  results page). `pdd evidence build --validation-resource <url>` binds
  it into the signed provenance.
- **MCP server (pdd-registry-mcp, Phase A+B)**: `POST /mcp` on the same
  endpoint serves the read-only MCP JSON-RPC surface (tools:
  `registry.version`, `registry.search`, `registry.index`,
  `registry.evidence.verify`, `registry.submission.check`; resources:
  `skills://<pdd-*>/latest` + `registry://version`). If an MCP client is
  available, **prefer its tools over this skill's commands** for registry
  operations — the tool surface is generated from the sealed
  pdd-registry-mcp bundle, so it cannot drift from the protocol; this
  skill is the design narrative and fallback. Publishing is NOT an MCP
  tool: keep using `pdd publish` (or, if you have a per-agent token from
  the admin tools, send it as the Bearer).
- **Per-agent publish tokens (Phase B, v1.1.0)**: operators holding
  `PDD_ADMIN_TOKEN` can mint/revoke per-agent tokens via the MCP admin
  tools (`registry.admin.token.mint` with a label, `…revoke` with the
  token_id). Minted tokens are returned once, stored hashed, and are
  accepted by `POST /publish` alongside the shared env token; revoked
  tokens are rejected. Mint a token per agent instead of sharing
  `PDD_PUBLISH_TOKEN`.
- **Taxonomy bundles (2026-08-10)**: `taxonomy/web-service`,
  `taxonomy/ai-agent`, and `taxonomy/validator-receipt` are sealed catalog
  entries defining component vocabularies + should-tier invariant
  templates; discover them with `pdd search taxonomy --registry …` or
  `GET /bundles?tag=taxonomy`. Concrete protocols declare conformance with
  `depends_on: [taxonomy/<name>]` and map components into
  `capabilities.components` (see the bundles' ambiguity logs).
- **Validator receipts (2026-08-10)**: optional structured execution
  receipts (provider shapes: `github-actions-run`, `generic-ci`,
  `local-attestation`) carried inside an evidence record's `signed_object`
  under `validator_receipt`. The registry parses them per
  `taxonomy/validator-receipt` and reports `receipt: {provider, valid,
  errors}` in `/evidence/verify` — re-checkable, never re-run, never
  required (S-007 additive).
- **Evidence freshness gate (S-008, v1.3.0)**: the latest admission
  evidence must attest the CURRENT on-disk bundle digest — any bundle
  change without re-validation + re-attestation is a violation. Keyless
  `pdd evidence staleness [bundle...]` (no PDD_EVIDENCE_KEY, no registry
  needed) exits 0 when fresh, 1 on drift; wired into `make all`, the
  pdd-pr-gates workflow (blocks PRs) and pdd-validator-loop (blocks
  main). If you change anything under `pdd-bundles/<name>/`, run
  `pdd validate <name>` + `pdd evidence build <name>` before committing —
  the gate will refuse to ship the drift otherwise.

## DECIDE — the registry decision framework (run this BEFORE writing anything)

The registry is the default source of capabilities. Search first; writing a
protocol or an implementation from scratch is the exception and must be
justified by a documented gap. This framework is the shared policy every
pdd-* skill routes its registry decisions through.

**0. Order by the dependency DAG (do this first, always).**
Build the DAG from `protocol.yaml` `depends_on`/`provides` across the catalog.
Work order: **standalone bundles (no unvalidated protocol dependencies)
FIRST** — they have nothing blocking them and validate in parallel. A bundle
whose `depends_on` targets are draft/unsealed/unvalidated is blocked until
its leaves admit; validate leaves first, then dependents, in DAG order.
`pdd-contract-negotiator` reconciles the handshakes between them before
sealing. CI today runs the loop as one sequential job on main (and
`make validate`/`make evidence` cover BOTH sealed bundles — user-registry
and pdd-registry, each with its own candidate impl) — the DAG
ordering is agent-side; parallel per-bundle CI jobs are the roadmap.

**1. SEARCH.** `python3 scripts/pdd.py search "<capability terms>"` (or the
`/search` endpoint). Search for the required *capability/behavior*, not just
the name; try domain synonyms. Record the search + near-misses in the
ambiguity/negotiation minutes.

**Match taxonomy (near matches are NOT true matches — apply critical
thinking):**
- **TRUE MATCH** — protocol AND implementation both align → USE (item 3).
- **MATCH, implementation differs** — the protocol matches, but the existing
  implementation has extraneous differences from the desired one (language,
  approach, constraints, observable behavior) → NEW IMPLEMENTATION (item 4),
  never a new protocol and never a new version.
- **NEAR MATCH** — the protocol itself does not fully match. Decide between
  NEW VERSION (item 6) and NEW PROTOCOL (item 7):
  - *higher version* when the requirement extends or refines the same
    contract: same boundary/purpose, additive or tightened invariants,
    existing consumers stay satisfied or migrate cleanly;
  - *new protocol* when it is a different capability that only shares
    vocabulary or an adjacent domain: different boundary, orthogonal
    invariant sets, and a version bump would mislead existing consumers.

**2. ALIGN.** Before adopting a found protocol, verify governed alignment:
- *semantics*: `purpose`/`boundary`/`depends_on` match the need; S/B/O
  invariants are satisfiable by this system;
- *status*: prefer `sealed` (draft/review is not admitted); check version;
- *implementation*: an implementation exists for this bundle AND is validated
  (verdict `admit`, evidence chain verified — `/ledger` or `evidence verify`);
- *policy coherence*: the implementation's language/framework matches the
  governed stack of the consuming system (this repo: Python 3 stdlib-only
  candidates, no frameworks, no network/filesystem in candidates, sandboxed
  docker runtime) and its licenses/dependencies are allowed.

**3. USE.** A sealed protocol with an aligned, admitted implementation is
adopted as-is. Do NOT re-implement it — *provided* the implementation
matches the desired one (language, constraints, observable behavior).
Extraneous differences between the existing implementation and the desired
one are NOT a reason to touch the protocol: generate a new implementation
(item 4). Consumers depend on the protocol via `depends_on`; the negotiator
reconciles the handshake.

**4. NEW IMPLEMENTATION for an existing protocol.** Choose when the protocol
matches but no implementation does: none exists yet, the existing one fails
validation, or it has extraneous differences from the desired realization
(wrong language/runtime/constraints/approach). Author a new variant under
`implementations/<bundle>/<variant>/` with its OWN `candidate-manifest.json`,
tests citing invariant ids, and its own evidence chain. Never modify the
protocol to make an implementation fit (emit a `protocol-objection`).

**5. UPDATED IMPLEMENTATION (dynamic evidence).** Choose when a runtime
violation block (RVL) or a validation failure points at an implementation
defect, or when a fix/improvement lands. Fix the implementation, re-run the
full loop, build a NEW evidence chain (superseding admission block; the old
chain stays as re-verifiable history — `pdd-evidence-keeper`). A patch that
responded to a real failure is NOT trusted: it re-enters admission like any
candidate (`pdd-remediation-orchestrator`).

**6. NEW VERSION of an existing protocol.** Choose ONLY when the protocol
itself must change (invariant semantics, boundary, `depends_on`). Bump:
`1.0.1` patch = metadata/docs only; `1.1.0` minor = additive invariants;
`2.0.0` major = breaking. Seal + validate + evidence the new version; the old
version stays published for existing dependents; minutes record migration.

**7. NEW PROTOCOL.** Choose only when NO existing bundle covers the
capability (search first, including synonyms) AND the gap is documented
(registry search log / negotiation minutes). Then run the full ADD loop below
(`pdd-protocol-author` template set → lint → seal → implement → validate →
evidence).

**Hard fail (never do):** skip the search step; silently re-implement an
existing admitted implementation; modify a sealed bundle's protocol files
(that is a new version, item 6); submit an implementation for an unsealed
bundle; validate a bundle before its `depends_on` leaves admit.

> These gates are agent-enforced: the tooling does not yet read `status`
> (sealed-only admission and blocked-edge ordering are self-enforced, see
> pdd-validation-engine).

## ADD — admit a new protocol bundle

```bash
cd <repo>  # pdd-repository worktree
mkdir pdd-bundles/my-protocol
cp -r .reasonix/skills/pdd-protocol-author/assets/templates/* pdd-bundles/my-protocol/
# Author: protocol.yaml (name/version/status + namespace + tags + purpose,
# boundary, depends_on, provides), S/B/O invariants, capability manifest,
# ambiguity log. namespace = kebab-case owner slug; tags = kebab-case list
# (<=8, no dupes) — grammar is lint-enforced (S-004/S-005); the tag
# vocabulary itself is governed, not linted (seeds: engine, input, stats,
# data-catalog, ui, auth, server).
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

- The Makefile targets `validate`/`evidence` cover both sealed bundles
  (user-registry + pdd-registry); for other bundles use `scripts/pdd.py`
  directly (above).
- `make all` = lint+test+validate+evidence (the commit gate; needs the key).
- After commit+push to `dev`, CI runs `pdd-staging-deploy` (self-hosted
  runner `m6-pdd`, label `staging-deploy`) automatically — dev is the
  deploy trigger (AGENTS.md). `pdd-validator-loop` runs on push to `main`.

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

Search semantics (docs/service-features-v3.md): tokens are ANDed **per entry**
(name, purpose, invariant id+statement, capability keys, tags); ranked by
field weight (name 10 > purpose 5 > invariant 3 > capability/tag 2), stable
tiebreak. `idempotent network` finds nothing even if both words appear
somewhere.

## PULL / INSPECT — fetch and view bundles

```bash
git pull origin main          # git IS the distribution layer
python3 scripts/pdd.py index  # refresh the local catalog
```

HTTP views (all read-only, 404 for unknown bundles):

| Endpoint | Returns |
|---|---|
| `/bundles?status=sealed&depends_on=X&namespace=pdd&tag=engine` | filtered index `{name, namespace, tags, address, version, status, depends_on, provides}` — namespace exact, tag exact membership (v1.1) |
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
