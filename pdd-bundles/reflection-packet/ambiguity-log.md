# Ambiguity Log — reflection-packet

## Resolved Assumptions

- **The packet is derived-only and mechanically assembled.** It introduces no facts
  beyond the annotation layers + baselines (invariant derived-only), and it is built
  by a non-LLM, no-network aggregator (invariants no-network, no-model-calls). The
  narrative content comes from the topic-flow-review pass; the packet does not
  re-summarize the transcript.
- **The anti-pollution boundary is a hard invariant.** The packet may contain only
  short supporting quotes from annotations, never bulk raw transcript (invariant
  no-raw-transcript). This is the interface that keeps raw detail out of the memory
  plane while giving reflection agents everything derived.
- **Heatmap is part of the packet, not the store.** The matrix is chunk-bucketed
  (x = chunk ids, so column width tracks content), colored by
  deviation-from-baseline (not raw counts), with null cells for lossy/absent data
  (invariant fidelity-flagged). Renders are text/HTML blocks (no image dependency);
  an image renderer is an optional later upgrade, not part of this bundle.
- **Provenance is complete.** Every aggregated record traces to pass_id +
  pass_version; baseline_refs list the per-model baseline versions used (invariant
  packet-provenance-complete), so the packet is auditable and regenerable.
- **Deterministic build** (invariant deterministic-build) — same layers + baselines
  → identical packet; the packet is a pure function of the store, reproducible for
  evidence.

## Open Questions

- **Baseline snapshot lifecycle** — how baselines are versioned and refreshed
  (per-model, per-version, per-session-type). This is meta-layer state that changes
  as the corpus grows; a stable baselines_ref scheme is needed before sealing.
- **Top-K case-study candidate limiting** — whether the packet caps candidates (from
  topic-flow-review) at a fixed K. Deferred to aggregation policy; candidate for v0.2.
- **Cross-session packet views** — whether a multi-session "digest" (aggregating
  several packets using the topic-graph) is in scope later; currently the packet is
  per-session. Likely a future bundle.

## Rejected Interpretations

- **The packet builder generating new analysis or narratives via an LLM** — rejected;
  assembly is mechanical (no-model-calls) and narratives come from flow-review.
- **Including raw transcript excerpts beyond short quotes** — rejected (no-raw-transcript);
  that is exactly the pollution the packet exists to prevent.
- **Storing the heatmap as first-class annotation records** — rejected; it is a
  derived view computed from the store, regenerable, not additional ground truth.
- **Performing cross-session aggregation here** — rejected; out of scope (topic-graph
  + a possible future digest bundle).
