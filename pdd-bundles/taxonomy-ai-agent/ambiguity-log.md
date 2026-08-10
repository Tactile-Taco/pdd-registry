# Ambiguity Log

- **Composition.** ai-agent composes web-service via `depends_on` — a full
  agent-with-web-surface protocol declares
  `depends_on: [taxonomy/web-service, taxonomy/ai-agent]`. The composed
  vocabulary applies to the web-facing parts; the agent vocabulary to the
  agent parts. The registry stores the edge; no conformance engine exists
  (vocabulary + templates only).
- **Should-tier templates by design** (same rationale as
  taxonomy/web-service): templates are opinion, adaptable with documented
  overrides; never silently dropped.
- **Overlap with web-service.** Components like `api`/`authn` belong to the
  web-service vocabulary; agents reuse them via composition rather than
  duplicating them here.
