# Negotiation Minutes — pdd-registry

Sealed via `pdd bundle seal`.
No open conflicts; lint passed; versions pinned.

## v1.1.0 version event (namespace/tags)

- **Fields added**: every bundle now declares `namespace` (kebab-case, e.g.
  `pdd`, `user`) and `tags` (kebab-case list, ≤8, no duplicates) in
  protocol.yaml. Additive, optional-fields-only — a minor version per S-003.
- **Addressing**: display address becomes `namespace/name`; on-disk layout
  stays `pdd-bundles/<name>` and evidence stays name-keyed — a
  backwards-compatible bridge, no directory reorganization.
- **Invariants**: new S-004 (name unique within a namespace; catalog must
  never contain two entries with the same (namespace, name) pair) and S-005
  (tag grammar: kebab-case, ≤8, no dupes, exact-membership filtering).
- **Enforcement**: `bundle-linter` (grammar + cross-bundle uniqueness via
  `pdd bundle lint`) and `contract-runner` (candidate + service suites).
- **Service surface**: `GET /bundles?namespace=X&tag=Y` exact-match filters;
  `namespace`, `tags`, `address` exposed in index/summary views.
- **Vocabulary**: seed vocabulary {engine, input, stats, data-catalog, ui,
  auth, server}; extensions are deliberate, documented changes. `auth` is
  reserved for authentication protocols (user-registry explicitly excludes
  auth and is tagged `server` only).
- **Compatibility**: name-only addressing (v1.0.0) keeps working; old
  clients see additive fields only.
- **Evidence naming (process)**: admission/discovery objects are stem-keyed
  `{impl[:16]}-{bundle[:12]}.{evidence,discovery}.json` so a version event
  writes a NEW object and never overwrites a prior version's attestation;
  old objects and old ledger blocks both stay (append-only).
- **Incident record (2026-08-08, resolved)**: the user-registry v1.0.0 admission
  object was superseded during the initial 1.1.0 version-event build —
  performed before the stem-keyed naming above existed — so its file
  temporarily disappeared from disk while ledger block 1 still attested its
  digest. The original bytes were recovered from git history
  (admission/5614cd8f49224f28.evidence.json + its discovery log; both digests
  match the ledger and the signed discovery binding) and restored, so the
  full chain now verifies with no dangling attestations. The stem-keyed
  naming makes recurrence impossible.

## v1.2.0 version event (DB-backed registry, author-owned validation)

- **Decisions (2026-08-09, user-confirmed forks)**: PostgreSQL in the staging
  k3s cluster on the M6 mini-pc hosts the registry database; the CLI
  resource identifier is a `pdd+http://` registry URL (same commands, new
  resource); the registry does NOT own a git repo of protocols — authors
  publish bundles + signed evidence; validation is author-owned (honor
  system), evidenced by `resource_identifier` records pointing at the
  author's validator-loop execution (e.g. CI/CD results page).
- **Version**: 1.2.0 minor (S-003: additive optional surface only —
  publish handshake, DB store, resource identifiers; v3 client surface
  unchanged; nothing removed/renamed/made-required).
- **Invariants**: S-006 (DB-backed storage, transactional consistent reads),
  S-007 (evidence resource_identifier provenance), B-006 (idempotent
  publish by (namespace, name, version, bundle_digest)).
- **Compatibility matrix**: pdd-registry 1.2.0 (1.1.0 in git history),
  sealed, provides pdd-registry.search + bundle-views (unchanged) +
  pdd-registry.publish (new). No dependents; no conflicts.
- **Enforcement**: storage/publish contract tests (contract-runner),
  publish schema strictness (json-schema), lint unchanged.

## v1.3.0 version event (evidence-freshness gate)

- **Decision (2026-08-09)**: after the DB-backed migration, a real drift
  surfaced — a publish-schema fix (8be0b55) changed the bundle directory
  AFTER the last evidence build, so the migrated registry record held the
  post-fix digest while the admission chain pinned the pre-fix snapshot.
  The freshness rule is a registry/evidence contract (external agents that
  interact with the registry must know that a bundle change without
  re-attestation is a violation), so it belongs IN the protocol, not just
  in CI.
- **Version**: 1.3.0 minor (additive invariant only — no handshake/surface
  change, nothing removed/renamed/made-required; same precedent as v1.1.0
  adding S-004/S-005).
- **Invariant**: S-008 (must, structural) — the latest admission evidence
  must attest the current on-disk bundle digest; any bundle-directory
  change without re-validation + re-attestation is a violation.
- **Enforcement (honesty rule — never pass what isn't enforced)**:
  keyless `pdd evidence staleness` CLI command (no PDD_EVIDENCE_KEY
  needed), wired into `make all` (between validate and evidence), the
  pdd-pr-gates workflow (blocks PRs) and the pdd-validator-loop workflow
  (blocks main); the existing `pdd evidence build` stale-results gate
  remains the signing-time backstop. The gate is exercised by
  test_evidence_staleness_fresh_then_drifted.
- **Compatibility matrix**: pdd-registry 1.3.0 (1.2.0 in git history),
  sealed, same provides (search, bundle-views, publish); no dependents.
- **Dogfood**: the version event itself changed the bundle digest — the
  gate failed on our own change until validate + evidence build re-ran,
  exactly as designed.

## v1.4.0 version event (ledger durability + version-event preservation)

- **Decision (2026-08-09)**: two migration/persistence gaps surfaced by the
  v1.3 staging deploy are sealed as structural `must` invariants. Gap 1: the
  registry-side ledger's durability/append-only property was only implicit
  (under S-006's generic "persisted"); the author-side evidence chain's
  append-only was explicit, the DB ledger's was not. Gap 2: B-006 guarantees
  "never a silent overwrite" only in the negative (no overwrite); nothing
  stated the positive requirement that a prior version stays fully queryable
  and verifiable after a version event re-publishes the same (namespace,
  name).
- **Version**: 1.4.0 minor (additive invariants only — no handshake/surface
  change, nothing removed/renamed/made-required; same precedent as v1.1.0
  adding S-004/S-005 and v1.3.0 adding S-008).
- **Invariants**: S-009 (must, structural) — registry-side ledger blocks are
  persisted in the backing database and append-only; existing blocks are
  never modified, deleted, or reordered; the hash-chained digest detects any
  tampering; a write that would alter a prior block must fail. S-010 (must,
  structural) — publishing a new version or bundle_digest for an existing
  (namespace, name) preserves the prior version's catalog record, evidence
  rows, and ledger blocks; prior versions stay queryable and verifiable.
- **Enforcement (honesty rule — never pass what isn't enforced)**: both are
  enforced by storage contract tests (`src/tests/test_registry_db.py`), not
  by the pure-core candidate (the candidate has no ledger/persistence). The
  adapter gains a public `verify_ledger_chain` that recomputes every block
  digest and re-walks the previous-links; tests tamper (UPDATE/DELETE/
  reorder) stored blocks via raw SQL and assert the chain verification fails.
  The candidate validator labels S-009/S-010 `skip`-with-reason pointing at
  the service contract tests (B-006 precedent), so no pass label implies
  enforcement that does not exist.
- **Compatibility matrix**: pdd-registry 1.4.0 (1.3.0 in git history),
  sealed, same provides (search, bundle-views, publish); no dependents; no
  conflicts. The DB-mode client surface is unchanged.
- **Dogfood**: the version event itself is the migration being exercised —
  validate + evidence build re-attest the new bundle digest (S-008 gate
  fails on our own change first, exactly as designed), and the staging
  deploy seeds the DB with the new digest + fresh evidence.
