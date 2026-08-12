# Ambiguity Log — topic-graph

## Resolved Assumptions

- **Index-first, incremental, O(N) per add.** Adding a session compares each new
  topic against the existing index once and never recomputes the whole corpus
  (invariant incremental-add). This is the decision from the growth analysis: batch
  recompute is O(N^2) per run and degrades as the corpus grows; index-first is
  O(N) per session. Enforced by a work-budget test, not just a preference.
- **Node identity is the (session-key, topic_id) composite** (e.g. `filename::t0`).
  Labels are per-session; cross-session vocabulary unification is represented by
  similarity *edges*, not by renaming nodes to a global label (that would be a
  lossy merge). The reflection layer can cluster via edges when it needs a global
  topic view.
- **Embeddings are local and non-generative.** The add computes embeddings locally
  and does no LLM reasoning (invariant no-llm-reasoning). This keeps the graph add
  cheap and non-LLM, matching the heatmap's non-LLM philosophy.
- **Edges are threshold-gated and deterministic** given fixed embeddings +
  threshold (invariant similarity-bound). Threshold default 0.7.
- **The vector-store migration is an observable, not a decision in this bundle.**
  index_size is logged after every add (invariant index-size-logged); at >100K
  nodes the implementation logs a migration-trigger observation (should,
  monitoring). The actual sqlite-vec/ANN migration is a later, separate decision
  driven by that observable.
- **In-memory numpy brute-force is the reference posture** for hundreds of
  thousands of vectors (memory-budget should: 4 GB). A store is an implementation
  choice; the protocol constrains capability (writes only to <graph-store>).

## Open Questions

- **Similarity metric** (cosine vs others) and whether to use label embeddings vs
  topic-span text embeddings — implementation detail to confirm when embeddings are
  wired.
- **Graph persistence format** (JSON/GraphML vs sqlite recursive CTEs) — deferred
  to implementation; capability manifest allows any write under <graph-store>.
- **Whether revival/overlap edge types are derivable cross-session** given only
  per-session transitions — likely requires the flow-review output too; may promote
  to a v0.2 dependency on topic-flow-review if cross-session revival matters.

## Rejected Interpretations

- **Global label canonicalization / node renaming across sessions** — rejected;
  represented as similarity edges instead (lossless).
- **Batch full-corpus recompute on each add** — rejected (incremental-add).
- **Generative LLM calls in the add** — rejected (no-llm-reasoning); keeps the
  graph add non-LLM and cheap.
