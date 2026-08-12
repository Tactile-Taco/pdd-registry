# Ambiguity Log — uncertainty-pass

## Resolved Assumptions

- **Marker taxonomy is model-agnostic; interpretation is not this pass's job.** The
  pass emits raw marker densities per 1K chars. Per-model / per-version /
  per-session-type baselines live in the meta layer, and deviation-from-baseline is
  computed downstream. This pass deliberately does not normalize against any model.
- **Two layers, one pass.** (a) Within-turn reasoning markers (hesitation/doubt in
  `reasoning_content`) — only available for sources that archive reasoning (reasonix,
  hermes); the pass emits an empty reasoning layer for other sources (invariant
  source-segregation). (b) Dialogue-level contention (user pushback, reverts,
  repeated corrections, negation in user turns) — available for all sources. This
  split mirrors the two signals discussed: epistemic uncertainty vs workflow tension.
- **Lexicons are pinned assets, not protocol.** The concrete marker lists (Kyoto
  marker lexicon, BioScope cues, MPQA, Loughran-McDonald) are assets referenced by
  `lexicon_version`; the protocol pins that a version is declared and cited, not the
  lexicon contents. This lets lexicons be tuned (their own churn) without a protocol
  bump.
- **Density includes diversity and variance** (not just mean), per research: Gravity7
  found incorrect traces show higher marker *diversity*; Cognaptus found higher
  *variance* of exploratory moves. Both are in the density schema.
- **Positional grammar** is the median marker position as percent through the chunk
  (ImpossibleBench-style), enabling front-loaded vs back-loaded doubt analysis.
- **The pass is read-only compute.** It returns records; the runner appends them via
  annotation-store. This keeps the pass stateless and re-runnable, and keeps the
  annotation store's append-only invariants authoritative.

## Open Questions

- **Revert-action detection.** Contention via explicit tool/action metadata (revert,
  retry) requires the transcript to record actions; sources that don't (kimi wire,
  some dumps) fall back to text-only contention signals. Whether to require action
  metadata for the "revert" contention signal is open.
- **Lexicon versioning scheme** (semver? content hash?) — pick when the first lexicon
  asset is bundled.

## Rejected Interpretations

- **Using an LLM or network in this pass** — rejected; the zero-cost, deterministic,
  non-LLM character is a core invariant (this is the "cheap-first" pass).
- **Normalizing density against baselines in this pass** — rejected; baselines are
  meta-layer state that changes as the corpus grows, and must not be coupled to a
  deterministic pass.
- **Emitting a single combined "uncertainty" value** — rejected in favor of the
  layered density schema so the heatmap rows (uncertainty vs contention) and
  deviation-from-baseline coloring stay meaningful.
