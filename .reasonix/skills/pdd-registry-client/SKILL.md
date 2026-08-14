---
name: pdd-registry-client
description: "Client for the PDD protocol registry: index, search, pull and inspect bundles, verify evidence, and publish bundles with signed evidence via the pdd CLI and HTTP API; owns the DECIDE use-vs-author decision framework."
---

# pdd-registry-client

## Naming — instance vs repository (do not conflate)

"M6 pdd-registry"         = the RUNNING instance: deployed service on the M6's
                            staging guest (k3s microvm), tailnet-only, HTTP
                            endpoint. https://staging.tail4904d2.ts.net — the
                            tailnet node name: MagicDNS-resolvable from any
                            tailnet node and served with a publicly-trusted
                            tailscale cert (no -k, no address pinning needed).
"pdd-registry repository" = the GIT repo Tactile-Taco/pdd-registry: protocols,
  / "pdd-registry github"   implementations, evidence, server, CI.
  / "pdd-registry git repo"

Rule: "repository" / "github" / "git repo" / "checkout" / "worktree" → the repo.
"instance" / "service" / "deployed" / "endpoint" / "host" → ALWAYS "M6 pdd-registry".
Bare "pdd-registry" only when unambiguous (protocol bundle name, skill name).

Operate a **PDD protocol registry**: search the catalog (CLI and HTTP),
pull/inspect bundles, invariants, capabilities, and evidence ledgers, admit
new protocol bundles, and publish them with signed evidence. The registry is
read-only over HTTP except POST /publish; **git is the distribution layer for
authoring** (no pull over HTTP — a documented design decision). The client
tooling is the **pdd CLI** (repo `Tactile-Taco/pdd-cli`, installed as `pdd`,
`pip install "pdd-cli @ git+https://github.com/Tactile-Taco/pdd-cli.git"`).

## Tooling — two namespaces, one binary

- `pdd workflow ...` — the AUTHOR-SIDE loop (offline, path-based): init, lint,
  seal, validate, evidence build/verify/package, run, staleness, status.
  Operates on local bundle/impl/evidence directories in a workspace (a tree
  containing `pdd-bundles/`); needs NO registry and NO checkout of the
  registry repo.
- `pdd registry ...` — the CLIENT (HTTP): search, index, inspect, verify,
  publish. Talks to the configured instance: `$PDD_REGISTRY` > config file
  (`~/.config/pdd/config.json`, `pdd config set-registry`) > default
  (`https://staging.tail4904d2.ts.net`).
- `pdd config show|set-registry` — endpoint resolution (secrets stay in env:
  `PDD_EVIDENCE_KEY`, `PDD_PUBLISH_TOKEN`).

## Layout (what the registry contains)

| Path | Role |
|---|---|
| `pdd-bundles/<name>/` | the registry: sealed protocol bundles (protocol.yaml, invariants/{S,B,O}.yaml, capability-manifest.yaml, evidence-requirements.yaml, validators/, ambiguity-log.md, negotiation-minutes.md) |
| `implementations/<name>/<variant>/` | candidate realizations (candidate-manifest.json + tests with invariant lineage) |
| `evidence/<name>/` | signed admission evidence + discovery logs, validation results, runtime ledger |
| `published/<name>/` + `published/<ns>/<name>/<version>/` | server-side published bundle store (live catalog merge; latest + immutable version snapshots) |
| `pdd.db` | SQLite: publish idempotency + submission history (stdlib sqlite3) |
| `src/server.py` + `src/registry_index.py` | the HTTP service (read views + POST /publish) |
| Makefile, CI, Dockerfile | call the installed `pdd` binary — the loop tooling (linter, engine, evidence chain) lives in the pdd-cli package, single source |

The catalog is built **live** from `pdd-bundles/*` (git checkout) **merged with**
`published/*` (server-written submissions). Runtime-written data lives on a
PersistentVolumeClaim, so rollouts survive. The instance runs the server from
`src/server.py`; `PDD_EVIDENCE_KEY` and `PDD_PUBLISH_TOKEN` come from cluster
Secrets created at deploy time.

## Access modes

The CLI's registry namespace talks to the instance over HTTP
(`--registry URL` or `$PDD_REGISTRY`); the workflow namespace operates on the
local checkout/bundle dirs (authoring is git-based — clone the repo and work
in `pdd-bundles/`, or keep bundles in your own workspace).

- **Publishing**: `pdd registry publish <bundle-dir> --evidence <file>
  [--registry URL] [--token-env NAME]` submits a bundle + its signed evidence
  (idempotent by (namespace, name, version, bundle_digest, evidence_digest)).
  Publishes are authenticated with a bearer token: the CLI sends
  `PDD_PUBLISH_TOKEN` (or `--token-env NAME`) when set; unauthenticated
  publishes are rejected 401. Registry-owned namespaces (`pdd`, `user`,
  `taxonomy`) additionally require the evidence's digest to be HMAC-signed
  with the registry's evidence key (the signed admission objects
  `pdd workflow evidence build` produces) — a token holder cannot squat them
  with an unsigned/stub object (publish 400). The registry does NOT own a git
  repo of protocols; the author-side git chain stays the source of truth.
- **Discovery binding**: the publish payload carries the discovery-log content
  whose digest the signed evidence provenance binds; the registry verifies the
  binding before storing, so `/evidence/verify` passes for published bundles.
- **Author-owned validation (honor system)**: the registry does not run the
  validator loop and does not prove validation. Every evidence record carries
  a `validation_resource` — an http(s) URL or `urn:` pointing at the author's
  validator-loop execution record (e.g. a CI results page).
  `pdd workflow evidence build --validation-resource <url>` binds it into the
  signed provenance.
- **Taxonomy bundles**: sealed catalog entries defining component
  vocabularies + should-tier invariant templates; discover them with
  `pdd registry search taxonomy` or the tag filter. Concrete protocols
  declare conformance with `depends_on: [taxonomy/<name>]` and map components
  into `capabilities.components` (see the bundles' ambiguity logs).

## DECIDE — the registry decision framework (run this BEFORE writing anything)

The registry is the default source of capabilities. Search first; writing a
protocol or an implementation from scratch is the exception and must be
justified by a documented gap. This framework is the shared policy every
registry decision routes through.

**0. Order by the dependency DAG (do this first, always).**
Build the DAG from `protocol.yaml` `depends_on`/`provides` across the catalog
(`pdd registry index`). Work order: **standalone bundles (no unvalidated
protocol dependencies) FIRST** — they have nothing blocking them and validate
in parallel. A bundle whose `depends_on` targets are draft/unsealed/unvalidated
is blocked until its leaves admit; validate leaves first, then dependents, in
DAG order. The contract-negotiator skill reconciles the handshakes between
them before sealing. The DAG ordering is agent-side; CI may run the loop as one
sequential job, and parallel per-bundle CI jobs are the roadmap.

**1. SEARCH.** `pdd registry search "<capability terms>"` (or the `/search`
endpoint). Search for the required *capability/behavior*, not just the name;
try domain synonyms. Record the search + near-misses in the
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
  (verdict `admit`, evidence chain verified — `/ledger` or
  `pdd registry verify`);
- *policy coherence*: the implementation's language/framework matches the
  governed stack of the consuming system (e.g. stdlib-only candidates,
  sandboxed runtime) and its licenses/dependencies are allowed.

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
chain stays as re-verifiable history). A patch that responded to a real
failure is NOT trusted: it re-enters admission like any candidate.

**6. NEW VERSION of an existing protocol.** Choose ONLY when the protocol
itself must change (invariant semantics, boundary, `depends_on`). Bump:
`1.0.1` patch = metadata/docs only; `1.1.0` minor = additive invariants;
`2.0.0` major = breaking. Seal + validate + evidence the new version; the
old version stays published for existing dependents; minutes record
migration.

**7. NEW PROTOCOL.** Choose only when NO existing bundle covers the
capability (search first, including synonyms) AND the gap is documented
(registry search log / negotiation minutes). Then run the full ADD loop below
(pdd workflow: init template set → lint → seal → implement → validate →
evidence).

**Hard fail (never do):** skip the search step; silently re-implement an
existing admitted implementation; modify a sealed bundle's protocol files
(that is a new version, item 6); submit an implementation for an unsealed
bundle; validate a bundle before its `depends_on` leaves admit.

> These gates are agent-enforced: the tooling does not yet read `status`
> (sealed-only admission and blocked-edge ordering are self-enforced).

## ADD — admit a new protocol bundle

```bash
# Workspace = a tree containing pdd-bundles/ (e.g. a registry-repo checkout
# or your own project). The CLI operates on paths; no registry needed.
pdd workflow init pdd-bundles/my-protocol
# Author: protocol.yaml (name/version/status + namespace + tags + purpose,
# boundary, depends_on, provides), S/B/O invariants, capability manifest,
# ambiguity log. namespace = kebab-case owner slug; tags = kebab-case list
# (<=8, no dupes) — grammar is lint-enforced; the tag vocabulary itself is
# governed, not linted (seeds: engine, input, stats, data-catalog, ui, auth,
# server).
pdd workflow lint pdd-bundles/my-protocol      # must pass (gate)
pdd workflow seal pdd-bundles/my-protocol      # status: draft → sealed

# Implementation (candidate, never authoritative):
mkdir -p implementations/my-protocol/<variant>/tests
# candidate-manifest.json + tests that cite invariant ids (e.g. B-001, S-002)

# Three-layer validation (S schema/conformance, B property+mutation, O allowlist/sandbox):
pdd workflow validate pdd-bundles/my-protocol --impl implementations/my-protocol/<variant> [--sandbox]

# Evidence (needs the signing key — from the configured secret store):
export PDD_EVIDENCE_KEY=<from-secret-store>
pdd workflow evidence build pdd-bundles/my-protocol --impl implementations/my-protocol/<variant> \
  [--validation-resource <url-or-urn>]
pdd workflow evidence verify pdd-bundles/my-protocol

# Publish to the instance (bearer token, idempotent):
export PDD_PUBLISH_TOKEN=<token>
pdd registry publish pdd-bundles/my-protocol --evidence \
  evidence/my-protocol/admission/<prefix>.evidence.json
```

- The repo's commit gate (lint + test + validate + evidence) needs the key.
- CI typically deploys the registry on push to the deploy branch and runs
  the validator loop on push to main — check the repository's automation
  conventions.

## SEARCH — the catalog

CLI (HTTP client against the instance; requires pyyaml):

```bash
pdd registry index [--registry URL]   # JSON catalog: versions, statuses,
                                      # depends_on, provides, invariant
                                      # counts, capability keys
pdd registry search <query> [--registry URL]  # ranked matches {bundle, layer,
                                      # id, text, score}; exit 0 if ≥1 match
```

HTTP (pin the address if your shell cannot resolve MagicDNS; the tailnet
node name resolves from tailnet nodes):

```bash
curl -s "https://staging.tail4904d2.ts.net/search?q=idempotent"
```

Search semantics: tokens are ANDed **per entry** (name, purpose, invariant
id+statement, capability keys, tags); ranked by field weight (name 10 >
purpose 5 > invariant 3 > capability/tag 2), stable tiebreak. `idempotent
network` finds nothing even if both words appear somewhere.

## PULL / INSPECT — fetch and view bundles

```bash
git pull                   # git IS the distribution layer for authoring
```

HTTP views (all read-only, 404 for unknown bundles):

| Endpoint | Returns |
|---|---|
| `/bundles?status=sealed&depends_on=X&namespace=pdd&tag=engine` | filtered index `{name, namespace, tags, address, version, status, depends_on, provides}` — namespace exact, tag exact membership |
| `/bundles/{name}` | summary + `invariant_ids` per layer + `evidence_status` (verified/unverified/invalid/none) |
| `/bundles/{name}/invariants?severity=must` | full S/B/O invariant items (severity filter: must/should) |
| `/bundles/{name}/capabilities` | capability manifest (network, filesystem, secrets, …) |
| `/bundles/{name}/ledger?limit=N` | last N ledger blocks + `verified` (real HMAC-chain check; no key → `verified:false`) |
| `/evidence/verify`, `/evidence/admission` | per-bundle verification: `ok` true iff every record verifies; `status` per record |

`pdd registry inspect <bundle> [--invariants|--capabilities|--ledger]` wraps
the per-bundle views; `pdd registry verify <bundle>` exits 0 when the
bundle's `evidence_status` is verified.

## Evidence model

- **Evidence freshness gate**: the latest admission evidence must attest the
  CURRENT on-disk bundle digest — any bundle change without re-validation +
  re-attestation is a violation. Keyless `pdd workflow staleness [bundle-dir...]`
  (no key, no registry needed) exits 0 when fresh, 1 on drift; wired into
  commit gates and CI (blocks PRs and main). If you change anything under
  `pdd-bundles/<name>/`, run `pdd workflow validate` +
  `pdd workflow evidence build` before committing — the gate will refuse to
  ship the drift otherwise. Evidence build **re-attests on drift**: when the
  bundle digest changed since the last admission, it rebuilds and appends a
  new ledger block (append-only; the old block stays as history).
- **Evidence verification**: `/evidence/verify` verifies every admission
  record (digest recompute + HMAC against the server key). `/bundles/{name}`
  exposes `evidence_status`: `verified` (all records verify), `unverified`
  (records exist but fail), `invalid` (structurally broken), `none` (no
  evidence). Author-namespace publishes are accepted on structural validity
  (honor system — the server holds no key for the author), while
  registry-owned namespaces require HMAC-valid evidence at publish time.
- **Validator receipts (optional)**: structured execution receipts (provider
  shapes: `github-actions-run`, `generic-ci`, `local-attestation`) carried
  inside an evidence record's `signed_object` under `validator_receipt`. The
  registry parses them per the `taxonomy/validator-receipt` vocabulary and
  reports `receipt: {provider, valid, errors}` in `/evidence/verify` —
  re-checkable, never re-run, never required.

## MCP surface (planned, not yet deployed)

The MCP surface described in earlier revisions (JSON-RPC tools
`registry.search`, `registry.index`, `registry.evidence.verify`, …) is
**planned, not deployed** — the live read surface is the HTTP API above and
the `pdd registry` CLI. Publishing is a CLI/HTTP concern (`pdd registry
publish`), never an MCP tool.

Per-agent publish tokens (admin mint/revoke, `registry.admin.token.mint` with
a label, `…revoke` with the token_id) are the **planned direction** for
multi-agent attribution; v1 uses the shared `PDD_PUBLISH_TOKEN`. Minted tokens
would be returned once, stored hashed, and accepted alongside the shared env
token; revoked tokens rejected. Mint a token per agent instead of sharing the
shared token — once the admin surface ships.

## Gotchas

- **Never put the evidence key in argv/echo** — keep it in the environment
  (`PDD_EVIDENCE_KEY`, `PDD_PUBLISH_TOKEN`); the CLI reads them from env only.
- **Validation on a machine without the sandbox runtime** rewrites validation
  result files with `skip`, diverging from the attested sandbox-pass version
  — restore them with `git checkout -- evidence/`.
- **Required dependencies** (e.g. pyyaml) are pinned; without them the CLI/
  API fail closed with an explicit error.
- **The registry is public** — never put machine addresses or secrets in
  bundles, commits, or docs; fetch addresses from the secret store at use
  time.
- **Tailnet-only**: the instance URL is the tailnet node name; it resolves and
  verifies only from tailnet nodes (MagicDNS + tailscale cert).

## Provenance

Merged and generalized from the following source skill(s):
- `pdd-registry-client`
