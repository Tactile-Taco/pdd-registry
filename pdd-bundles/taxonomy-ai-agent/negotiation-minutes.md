# Negotiation Minutes — taxonomy/ai-agent

## v1.0.0 version event (taxonomy service, second bundle)

- **Decision (2026-08-10)**: second taxonomy bundle for AI-agent protocols,
  composing taxonomy/web-service via depends_on. Same shape as its parent:
  vocabulary in capabilities.components, should-tier templates in the
  structural invariants, vocabulary-validator candidate.
- **Version**: 1.0.0.
- **Invariants**: S-001..S-005 (templates: tool-call envelope, context
  boundary, guardrail ordering, provenance, component mapping) + B-001
  (vocabulary validator) + O-001..O-004.
- **Compatibility matrix**: taxonomy/ai-agent 1.0.0 (sealed), depends_on
  taxonomy/web-service 1.x; no dependents; no conflicts.
- **Enforcement**: contract tests + mutation sanity on B-001; bundle lint;
  import scan/sandbox.
