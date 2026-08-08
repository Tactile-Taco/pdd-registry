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
