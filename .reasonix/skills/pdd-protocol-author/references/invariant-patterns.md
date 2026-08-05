# Invariant Patterns

Use these patterns as starting points. Adapt the names, predicates, and validation mechanisms to the domain.

## Structural Invariants

```yaml
- id: schema-conformance
  statement: Requests and responses must conform to the referenced JSON Schemas.
  severity: must
  validation:
    - json_schema_validation
```

```yaml
- id: stable-error-envelope
  statement: All failures must return one of the enumerated error codes and include a correlation_id.
  severity: must
  validation:
    - contract_tests
```

```yaml
- id: backward-compatible-minor-version
  statement: Minor versions may add optional fields but must not remove or rename existing fields.
  severity: must
  validation:
    - schema_diff_check
```

## Behavioral Invariants

```yaml
- id: idempotent-request
  statement: Repeating a request with the same idempotency key must not create a second committed effect.
  severity: must
  property: "f(x, s) = (y, s') => f(x, s') = (y, s')"
  validation:
    - property_based_test
    - state_transition_test
```

```yaml
- id: deterministic-output
  statement: Given the same input and admissible state, the component must return the same protocol-visible output.
  severity: must
  validation:
    - replay_test
```

```yaml
- id: monotonic-risk
  statement: Adding adverse evidence must not lower the risk score.
  severity: must
  property: "evidence(x) subset evidence(y) => score(y) >= score(x)"
  validation:
    - property_based_test
```

```yaml
- id: invalid-input-fails-closed
  statement: Invalid input must return a typed error without committing side effects.
  severity: must
  validation:
    - fuzz_test
    - side_effect_trace
```

## Operational Invariants

```yaml
- id: no-outbound-network
  statement: The implementation must not initiate outbound network connections.
  severity: must
  validation:
    - sandbox_network_denial
```

```yaml
- id: bounded-database-writes
  statement: A single request may perform at most one database write.
  severity: must
  validation:
    - database_call_counter
```

```yaml
- id: latency-budget
  statement: p95 validation latency must not exceed 100 ms under the benchmark workload.
  severity: should  # project policy: performance invariants default to should, generous budgets
  infra_assumption: benchmark harness; excess recorded as observation, not admission failure
  validation:
    - benchmark
```

```yaml
- id: approved-dependencies-only
  statement: Runtime dependencies must come from the approved dependency allowlist.
  severity: must
  validation:
    - dependency_scan
```
