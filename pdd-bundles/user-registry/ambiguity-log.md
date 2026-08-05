# Ambiguity Log — user-registry v1.0.0

## Resolved Assumptions

- **Identity key.** Idempotency keys on `client_request_id` (client-supplied,
  1..128 chars), not on email. A repeated `client_request_id` returns the
  original record with `outcome: existing`.
- **Email uniqueness semantics.** Emails are unique after normalization
  (trim + lowercase). A second create with the same normalized email and a
  DIFFERENT `client_request_id` returns a typed `conflict` error and performs
  no write. Provenance: this resolves the paper's "email uniqueness and case
  normalization need a decision" ambiguity for the example handler.
- **Storage engine.** Any engine is permitted (in-memory store used in the
  python-stdlib candidate). The protocol constrains write counts, not engines.
- **Performance budgets are advisory.** O-005 is `severity: should` per project
  policy (provisional infrastructure); excess is recorded as an observation,
  not an admission failure.
- **Email charset.** Addresses are ASCII-only (`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`,
  optional surrounding whitespace trimmed). Rationale: case-folding of non-ASCII
  characters (e.g. `ß`.upper() = `SS`) is locale/library-dependent, which would
  make B-005's case-insensitive uniqueness ill-defined across implementations.
  Non-ASCII addresses are rejected as `invalid_request`.
- **Timestamp format.** `created_at` is UTC ISO-8601 with `Z` suffix
  (schema-pinned), so replay is deterministic across hosts.
- **Structural validation is defense in depth.** The implementation re-validates
  the request against the schema contract before any state change; the validator
  also validates independently.
- **display_name is stored as submitted** (any 1..200-char string); no trimming
  on the wire or at rest, so S-001 conformance is exact.

## Open Questions

- None blocking. (Potential future protocol versions may add: pagination,
  update/delete semantics, authz — all currently out of scope.)

## Rejected Interpretations

- **"Create if the user does not exist by email"** as the idempotency semantics —
  rejected: would make the same email concurrently creatable under different
  request ids, conflating idempotency (client retries) with uniqueness (email
  constraint). Kept as two distinct invariants: B-001 (request-id idempotency)
  and B-002 (email uniqueness).
- **Silent email case folding** without documenting it — rejected: B-005 makes
  normalization protocol-visible so clients can predict `conflict` behavior.
