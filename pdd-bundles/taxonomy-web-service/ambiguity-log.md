# Ambiguity Log

- **Should-tier templates by design.** All templates (S-001..S-005) are
  `severity: should`: a taxonomy encodes opinion, and concrete protocols
  must be free to adapt it with documented overrides (the performance-budget
  precedent, O-005 of pdd-registry). A template may be raised to `must` by a
  conforming bundle with a reason in its minutes; it may be narrowed, never
  silently dropped.
- **Conformance mechanism.** A concrete bundle declares
  `depends_on: [taxonomy/web-service]` (the registry already expresses the
  edge); component mapping is declared in its `capabilities.components`
  with names from this vocabulary. The registry itself performs no
  conformance check — that stays the concrete bundle's responsibility
  (and the taxonomy candidate validates the vocabulary SHAPE only).
- **`taxonomy` tag.** A new controlled-vocabulary seed, documented here
  (the registry's ambiguity log records that additions are deliberate and
  documented).
