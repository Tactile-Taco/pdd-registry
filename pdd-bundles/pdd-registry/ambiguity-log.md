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
