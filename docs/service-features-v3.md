# pdd Registry Server — Client-Facing Feature Enumeration v3 (namespace/tags)

Purpose: define the v1.1 protocol version event + its client surface BEFORE
changing the code, per the PDD methodology (docs/service-features-v1.md and
-v2.md were the same contract for earlier iterations). This is a **minor
version event** (S-003): two additive, optional-at-the-service metadata fields
plus exact-match filters — no field removed, renamed, or made required inside
the existing major version, and no directory reorganization.

## The iteration in one sentence

Give every bundle a **namespace** (owner/scope slug) and a **tags** list so
the registry gets structured addressing (`namespace/name`) and filtering
(`GET /bundles?namespace=typing&tag=engine`) — while the on-disk layout stays
flat (`pdd-bundles/<name>`) and evidence stays name-keyed.

## Why: naming conflicts for similar protocols

The registry has no method today to distinguish two similar protocols that
happen to share a name (e.g. a second typing project's `typing-test-engine`
next to ours). A globally-unique name registry would force arbitrary renames.
The v1.1 answer is **namespace-scoped uniqueness**:

- `name` must be unique *within* a `namespace`, not globally (S-004).
- The display address is `namespace/name` (Docker-Hub owner / npm scope
  analogy); the directory and evidence keys stay `name` (backwards-compatible
  bridge).
- The catalog builder fails closed on duplicate `(namespace, name)` pairs,
  and the linter enforces the grammar + uniqueness at authoring time.

## Fields (protocol.yaml, every bundle)

| Field | Type | Grammar (lint-enforced) |
|---|---|---|
| `namespace` | string | kebab-case, 1..63 chars, required |
| `tags` | list[string] | each kebab-case, at most 8, no duplicates, required |

The tag vocabulary is small and controlled; seeds: `engine`, `input`,
`stats`, `data-catalog`, `ui`, `auth`, `server`. Additions are deliberate,
documented changes (see pdd-registry negotiation minutes), not a lint gate —
the linter enforces shape, not membership.

## Client-facing surface (v3)

### HTTP — additive over v2

| Endpoint | Method | Returns | Notes |
|---|---|---|---|
| `/bundles?namespace=user&tag=server` | GET | filtered `{bundles:[{name, namespace, tags, address, version, status, depends_on, provides}]}` | new optional filters, exact-match; combinable with `status`/`depends_on`; prefix must NOT match |
| `/bundles/{name}` | GET | summary now also carries `namespace`, `tags`, `address` | additive fields |

`address` = `namespace/name` (bare `name` for a bundle without a namespace).

### CLI (`scripts/pdd.py`)

| Command | Returns | Notes |
|---|---|---|
| `pdd index` | catalog rows now include `namespace`, `tags`, `address` | additive |
| `pdd search <q>` | tag values are searchable entries (`layer: "tags"`) | additive, weight 2 like capabilities |
| `pdd bundle lint` | runs the catalog-wide lint: per-bundle grammar + `(namespace, name)` uniqueness | `check_bundle.py --catalog` |

### Enforcement chain

1. `bundle-linter` — `check_bundle.py` grammar checks (namespace kebab-case,
   tags kebab-case ≤8 no dupes) and the catalog-wide `(namespace, name)`
   uniqueness pass (S-004, S-005).
2. `registry_index.load_catalog` — marks every entry of a colliding
   `(namespace, name)` group as a broken/error entry (fail-closed: never
   silently served).
3. `schema-validator` — structural layer of the Validator Loop reports
   S-004/S-005 per bundle.
4. `contract-runner` — candidate + service suites: exact namespace/tag
   filters, address derivation, tag search entries.

## Explicitly NOT in v3 (deferred, with reasons)

- **`GET /bundles/{namespace}/{name}` route** — the flat layout keeps
  name-keyed routes; `namespace/name` is a display address + filter pair.
  A namespaced route belongs with a subdirectory layout.
- **`namespace/name@version` pull semantics** — versions already live in
  `protocol.yaml`; Docker-tag-style pinning has a natural home when a
  versioned-pull endpoint is specified (noted here so the gap is explicit).
- **Vocabulary enforcement in the linter** — the vocabulary is governed, not
  grammatically enforced; hard-coding it into the linter would turn a
  deliberate governance change into a code change.
- **Renaming/backfilling legacy bundles** — fields are additive; existing
  consumers of v1/v2 responses keep working unchanged.

## Acceptance checks (this doc is the contract)

1. `make lint` passes; a bundle with a bad namespace, duplicate tags, >8
   tags, or a duplicate `(namespace, name)` address fails lint with a clear
   message.
2. `pdd index` shows `namespace`/`tags`/`address`; `pdd search engine` finds
   the pdd-registry `tags` entry.
3. `GET /bundles?namespace=user` returns user-registry only;
   `GET /bundles?namespace=pdd&tag=server` returns pdd-registry only;
   `GET /bundles?namespace=use` and `?tag=eng` return nothing (exact match).
4. `GET /bundles/{name}` includes `namespace`, `tags`, `address`.
5. The candidate suite and service suite pass (`make test`), the Validator
   Loop admits both bundles (`make validate` for user-registry + `pdd
   validate pdd-registry`), evidence is rebuilt under the real
   `PDD_EVIDENCE_KEY`, `git diff --check` is clean, and a post-mutation
   review runs before the final answer.

---

# v1.2: DB-backed registry (PostgreSQL on the mini-pc)

Purpose: host the protocol registry on a **database** instead of the git
repo. The pdd-registry protocol moved to **1.2.0** (minor, S-003:
additive-only surface). The client surface (endpoints, filters, schemas for
search/views) is unchanged; the serving layer changed and the registry
gained a publish handshake. This section documents the v1.2 contract.

## Architecture decisions (user-confirmed, 2026-08-09)

- **DB engine**: PostgreSQL in the staging k3s cluster on the M6 mini-pc
  (persistent volume; manifests in `deploy/postgres.yaml`; the
  `pdd-postgres` Secret with `PDD_DATABASE_URL` is created idempotently by
  `deploy/push.sh`).
- **Resource identifier**: the CLI talks to the registry server with
  `--registry pdd+http(s)://<host>/` (or `$PDD_REGISTRY`) — the SAME
  commands (`pdd index`, `pdd search`, `pdd evidence verify`) against the
  DB-backed registry; the server reads the database
  (`PDD_DATABASE_URL` env, `src/registry_db.py`, sqlite for dev/tests,
  PostgreSQL in production — one portable SQL dialect).
- **Write path**: authoring/lint/seal/validate/evidence stay in git (the
  author-side chain is the source of truth); the DB is the serving layer.
  The registry does **not** own a git repo of protocols.
- **Validation is author-owned** (honor system in this version): the
  registry does not run the validator loop and does not prove validation.
  Every admission evidence record carries a `resource_identifier` (S-007)
  — an http(s) URL or `urn:` pointing at the author's validator-loop
  execution record (e.g. a CI/CD results page); the registry's
  `/evidence/verify` checks presence, format, decision, and signature only.
- **Deploy target**: the staging k3s guest on the M6 (existing ingress,
  tailnet-only).

## New surface (v1.2)

| Surface | Shape |
|---|---|
| `POST /publish` | `{bundle, evidence}` validated against `schemas/publish.schema.json`; idempotent by `(namespace, name, version, digest)` (B-006); evidence requires `resource_identifier` (S-007); requires `Authorization: Bearer <PDD_PUBLISH_TOKEN>` (pdd-publish-token Secret, created idempotently by push.sh) — unauthenticated publishes are rejected 401; only available in DB mode — the filesystem path never accepts writes over HTTP. **The token travels in the header: use `pdd+https://` for remote registries; `pdd+http://` is for localhost/tailnet-LAN only (the in-cluster seed uses localhost).** |
| `GET /bundles` (DB mode) | same v3 shape, materialized from the database (S-006) |
| `GET /search?q=` (DB mode) | same ranking, over the DB catalog |
| `GET /evidence/verify` (DB mode) | per stored record: presence + resource_identifier format + decision + signature (honor system) |
| `GET /bundles/{name}/ledger` (DB mode) | the registry's own append-only ledger table |
| CLI `pdd publish <dir> --evidence <file> --registry pdd+http(s)://…` | builds the publish payload (resource_identifier from the evidence file or its `provenance.validation_resource`) and POSTs it |
| CLI `--registry` / `$PDD_REGISTRY` | `pdd index` / `pdd search` / `pdd evidence verify` against a remote DB-backed registry |
| CLI `pdd evidence latest <name>` | prints the admission attested by the LATEST ledger block (used by `deploy/push.sh` to seed the DB) |
| CLI `pdd evidence build --validation-resource <url>` | binds the author's validator-loop record into the signed provenance (S-007 dogfood) |

## Seeding (git -> DB on deploy)

`deploy/push.sh` publishes each sealed bundle with the evidence attested by
its latest ledger block after each deploy (idempotent, B-006), so the
registry database mirrors the repo's catalog without the registry owning a
git repo.

## Acceptance checks (v1.2)

1. `pdd validate pdd-registry` admits with S-006/S-007 passing and B-006
   honestly skipped until its service contract tests land (they now exist
   in `src/tests/test_registry_db.py`).
2. `python3 -m pytest src/tests/test_registry_db.py` passes: publish
   idempotency (B-006), resource_identifier enforcement (S-007), dialect
   portability (`_adapt_sql`), invariant-layer filling, HTTP publish
   (schema rejection, filesystem-mode fail-closed), DB-mode
   evidence/ledger routes, CLI remote surface.
3. `deploy/postgres.yaml` + `deploy/k8s.yaml` parse with the documented
   invariants (traefik, `pdd-repository` identity, `PDD_DATABASE_URL` ->
   `pdd-postgres` secret, postgres Deployment/Service/PVC) — enforced by
   `test_deploy_manifests_parse`.
4. The evidence chain verifies: pdd-registry admission
   `ba2e5aa69ef93b42-fdd2918f267e` binds
   `provenance.validation_resource` (the pdd-validator-loop workflow) and
   all blocks verify under the real `PDD_EVIDENCE_KEY`.
