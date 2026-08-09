# Protocol-bundle taxonomies for common architectures (sketch)

**Idea.** Publish registry entries whose *purpose is vocabulary*, not a
concrete system: a taxonomy bundle defines the canonical components, tag
vocabulary, and `should`-tier invariant templates for a class of
architecture (web-service, ai-agent, message-queue, data-pipeline). Concrete
protocol bundles declare "follows taxonomy X" (a `depends_on` edge), so
cross-project invariants align without re-authoring.

**How it fits the registry today (zero new code to start).**
- Taxonomy bundles are normal sealed bundles with tag `taxonomy` (a
  deliberate controlled-vocabulary addition, per the ambiguity-log process),
  e.g. `taxonomy/web-service`, `taxonomy/ai-agent`.
- `depends_on` already expresses composition; search/filter already supports
  discovery (`?tag=taxonomy`).
- Namespace-scoped variants (`taxonomy/acme-web`) let orgs override the
  generic templates.

**Later (optional).** A resolver endpoint — `GET /taxonomies/<name>` —
returning components + referenced invariant templates in machine-readable
form, so agents can expand a taxonomy into per-component invariants.

**Cautions.**
- Taxonomies are opinionated: keep template invariants at `severity: should`
  (mirroring the performance-budget precedent) and require documented
  overrides.
- Version events on a taxonomy must not silently re-shape dependents —
  dependents pin the taxonomy version they follow (same S-003 discipline).

**Why deferred.** No consumer yet; the registry's core value today is the
registry itself. Natural next step after the MCP server (which could serve
taxonomy expansion to agents).
