# Ambiguity Log — topic-flow-review

## Resolved Assumptions

- **The review is intra-session.** It consumes one session's topic/transition
  (and optionally contention) layers and produces a flow report + findings for that
  session. Cross-session relationship belongs to the topic-graph bundle.
- **Derived-only.** The report must not introduce facts absent from the consumed
  layers (invariant flow-derived-only). The review reorganizes and interprets the
  annotations; it does not re-analyze the raw transcript. This keeps it grounded and
  bounded (it reads annotations, not the full render).
- **Findings are the payoff.** Tensions, skill-improvement candidates, and
  case-study candidates must each cite at least one supporting annotation
  (invariant finding-grounded). This is the link between annotation and the
  skill-review system the program exists to serve.
- **Contention input is optional.** tension identification can use the contention
  layer from uncertainty-pass when present; the review still runs on topics +
  transitions alone.
- **Model choice is configuration.** Same as topic-transition-pass: the bundle
  constrains capability (network to router only, call count <= 3/session, cost
  budget) but not which model. Determinism not required; supersede handles re-runs.

## Open Questions

- **How many findings is too many?** No cardinality bound yet — the consuming
  reflection-packet may impose one (e.g. top-K case-study candidates) as an
  aggregation policy. Candidate for v0.2.
- **Whether the flow narrative should be stored as an annotation** vs only in the
  packet — currently the narrative lives in the pass response and flows into the
  packet; only structured edges/findings are stored as annotations.

## Rejected Interpretations

- **Re-reading the raw transcript for the review** — rejected; the review reads
  annotations only (cheap + grounded), keeping it from becoming a second full
  LLM analysis.
- **Performing cross-session relationship or flow analysis here** — rejected;
  separate bundles.
- **Guaranteeing determinism** — rejected (LLM); supersede handles re-runs.
