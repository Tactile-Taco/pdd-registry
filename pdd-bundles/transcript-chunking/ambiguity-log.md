# Ambiguity Log — transcript-chunking

## Resolved Assumptions

- **Chunk unit is the turn.** Turns are never split: they are the atomic unit for
  both reasoning-block analysis (reasoning traces live inside single assistant
  messages) and dialogue-level contention (user/agent exchanges). A turn that would
  exceed target_chars is emitted as its own chunk (target is a soft size, not a hard
  cap). [criticality: cosmetic — all planned passes consume turns atomically]
- **Default chunk params:** `target_chars: 80000` (~20-40k tokens),
  `boundary_overlap_turns: 1`. Sized for the cheapest capable model tier in the
  free-failover chain. Overlap turns appear in the *review* layer, never as
  duplicated content in the canonical chunk map.
- **fidelity_class is derived from source**, not caller-supplied: full = reasonix,
  omp, hermes; lossy = claude, codex; kimi assumed full (raw wire data) until its
  compaction behavior is verified (see open questions).
- **The render body is not part of the handshake response.** The protocol-visible
  artifact is `render_id` + `render_sha256` + the chunk map with char offsets; the
  implementation materializes the canonical render into the chunk store. This keeps
  the response bounded for multi-MB transcripts while preserving verifiability via
  hashes.

## Open Questions

- **kimi fidelity class.** Verify kimi (wire/context) compaction behavior before
  sealing; a wrong fidelity tag would corrupt cross-source density comparisons.
- **Chunk-size defaults** may be tuned per pass without protocol change (explicit
  params), but the default set should be validated against the real corpus (759
  files, largest 22 MB) when the reference implementation lands.

## Rejected Interpretations

- **Splitting turns for size.** Would break reasoning-block analysis and
  dialogue-contention attribution; rejected.
- **Overlapping chunks in the canonical chunk map.** Overlap belongs to the
  annotation/review layers; a canonical map must be a strict partition of turns
  (invariant chunk-coverage).
- **Chunking via LLM or with network access.** Chunking is a zero-cost mechanical
  pass by design (invariants no-network, no-model-calls); any semantic grouping is a
  later pass's job (topic segmentation refines chunk boundaries downstream, never in
  this bundle).
