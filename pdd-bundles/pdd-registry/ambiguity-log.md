# Ambiguity Log — pdd-registry

Open items and recorded decisions for the pdd-registry protocol bundle.

## Open

- **Attested core vs production surface.** The candidate attests the pure
  in-memory catalog core; the HTTP server (`src/server.py`) and filesystem
  index (`src/registry_index.py`) are the deployment surface and are NOT part
  of the attested artifact (the candidate performs no file I/O by design,
  O-002). Decision pending: should a future version attest the server as a
  second implementation variant (needs the validator's smoke to drive HTTP,
  which the current sandbox contract does not support)?
- **Search semantics parity.** The candidate is the canonical definition of
  the search/view semantics; the production server delegates to the shared
  index (`src/registry_index.py`). The two are kept in lockstep by the
  service suite (`src/tests/test_registry.py`) but are not byte-identical
  implementations. If they ever diverge, the server is wrong, not the bundle.

## Resolved

- **No persistence in scope.** Catalog construction is the caller's concern
  (filesystem index, HTTP request parsing, deployment) — the protocol
  constrains the pure read core only.
- **Single variant.** One python-stdlib candidate for v1.0.0; a second
  variant may be added later without a protocol version bump.
- **DB-backed registry (v1.2.0).** The registry catalog + evidence records
  are served from a PostgreSQL database deployed in the staging k3s cluster
  on the M6 mini-pc; the CLI reaches it via a `pdd+http://` registry URL
  (resource identifier). The filesystem bundle layout remains one authoring
  format; the registry does NOT own a git repository of protocols.
- **Author-owned validation (v1.2.0).** The registry does not run the
  validator loop and does not prove validation (honor system in this
  version). Every admission evidence record carries a `resource_identifier`
  (http(s) URL/URN) pointing at the author's validator-loop execution
  record (e.g. a CI/CD results page); verification is limited to presence,
  format, and signature. Rationale: the protocol author owns the validation
  resource; the registry is the catalog + evidence store, not the harness.
- **Publish handshake.** New `pdd-registry.publish` handshake
  (schemas/publish.schema.json); idempotent by
  (namespace, name, version, bundle_digest) — B-006.
- **Minor (1.2.0) not major.** Client surface (v3 endpoints, schemas for
  search/views) is unchanged; additions are optional/additive
  (publish handshake, DB store, resource identifiers); nothing removed,
  renamed, or made required — S-003 minor-version rules hold. Recorded in
  negotiation minutes 2026-08-09.
- **Namespace/tags (v1.1.0).** Namespace is a kebab-case owner/scope slug
  (Docker-Hub-owner / npm-scope analogy); uniqueness is scoped to the
  (namespace, name) pair, NOT global — a hypothetical second typing project
  can carry its own `typing-test-engine` under its own namespace without
  colliding with ours. Tags come from a controlled vocabulary whose seeds are
  {engine, input, stats, data-catalog, ui, auth, server}; additions are
  deliberate and documented (see negotiation minutes).
- **Tag choice for user-registry.** Tagged `server` only; `auth` is reserved
  for authentication protocols — user-registry's boundary explicitly excludes
  authentication/authorization, so `auth` would overclaim.
- **S-008 "latest admission" definition.** "Latest" = the admission file
  whose content digest matches the evidence_digest of the LAST ledger
  block for the bundle (the same selection rule as `pdd evidence latest`,
  which drives the deploy-time DB seeding). Multiple implementation
  artifacts attested for one bundle: the latest block wins; re-attestation
  of the current digest for the canonical artifact is the requirement.
- **Staleness vs. un-attested bundles.** A bundle with no evidence chain
  is SKIPPED, not failed — the gate is about drift of attested bundles;
  the seal + evidence-build flow covers first-time attestation.
