# Negotiation Minutes — taxonomy/web-service

## v1.0.0 version event (taxonomy service, first bundle)

- **Decision (2026-08-10)**: the taxonomy-service sketch becomes real with
  two sealed taxonomy bundles (web-service here, ai-agent next). Taxonomies
  are catalog entries like any protocol — discovered via the existing
  search/filter surface (`?tag=taxonomy`) — with vocabulary in
  `capabilities.components` and should-tier templates in the structural
  invariants. The resolver endpoint from the sketch is deferred.
- **Version**: 1.0.0 (first sealed version).
- **Invariants**: S-001..S-005 (templates, should-tier) + B-001 (vocabulary
  validator: unknown components/template refs reported; deterministic,
  non-mutating) + O-001..O-004 (stdlib-only, no IO).
- **Compatibility matrix**: taxonomy/web-service 1.0.0 (sealed); no
  dependents; `taxonomy` tag seed documented in the ambiguity log; the
  ai-agent taxonomy depends on this bundle.
- **Enforcement**: contract tests + mutation sanity on B-001, bundle lint
  on the templates, import scan/sandbox on O-*.
