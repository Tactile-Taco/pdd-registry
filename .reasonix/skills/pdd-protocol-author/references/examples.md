# Examples

## Idempotent User Creation

Human requirement:

> Build an API that creates a user if one does not exist. It should be safe to retry and should not call external services.

Protocol interpretation:

- Boundary: user creation handler only.
- Structural: request must include `client_request_id`, `email`, and `display_name`.
- Behavioral: duplicate `client_request_id` must not create a second user.
- Operational: no outbound network, at most one database write, bounded latency.
- Ambiguity: email uniqueness and case normalization need a decision.

Example behavioral invariant:

```yaml
- id: idempotent-user-create
  statement: Repeating a request with the same client_request_id must return the originally committed user without creating a second user.
  severity: must
  property: "create(x, s) = (user, s') => create(x, s') = (user, s')"
  validation:
    - property_based_idempotency_test
    - database_write_counter
```

## Bounded ETL Normalizer

Human requirement:

> Normalize all valid transaction records and reject bad rows. It should run in streaming mode and avoid disk.

Protocol interpretation:

- Structural: input and output record schemas.
- Behavioral: each valid input maps to exactly one output unless filtering is explicitly enabled.
- Operational: no temporary disk writes, fixed memory bound, per-record latency budget.

Example operational invariant:

```yaml
- id: streaming-no-disk
  statement: The pipeline must process records in streaming mode without writing temporary files.
  severity: must
  validation:
    - filesystem_trace
    - memory_profile
```
