# Transcript Annotation Program (PDD)

Protocol-driven formalization of the transcript annotation pipeline: multi-pass
annotation of archived AI session transcripts to guide the reflection /
retrospective / case-study / skill-review system.

Status: program design in review. Bundles 1-2 authored (draft), bundles 3-6 to be
authored after boundary review. Nothing here is sealed.

## Corpus context (measured 2026-08-12)

- Archive: M6 `/home/tacticaltaco/transcript-archive` — 759 files, 237 MB, immutable
  (`chattr +i`; append-only by design). Sources: reasonix 144, kimi 416, hermes 188,
  omp 10, claude 1.
- Fidelity classes: `full` (reasonix/omp/hermes — append-only transcripts; kimi wire
  assumed full until verified) vs `lossy` (claude/codex — compaction rewrites).
- Growth: ~9 reasonix sessions/day + 1-2 hermes/day; projected 15 sessions/day →
  ~5,500 sessions/year. Topic-node index ≈ 8K today, ~55K/year.
- Largest file 22 MB (kimi wire, reasoning-free); the reasoning-rich sources
  (reasonix, hermes dump) are the compact ones.

## Pipeline (passes)

| # | Pass | Nature | Model |
|---|---|---|---|
| 0 | Chunking (render + chunk map) | mechanical | none (invariant) |
| 1 | Uncertainty/contention density | regex/lexicon + positional grammar; dialogue-level contention | none (invariant) |
| 2 | Topic + transition annotation | LLM (free-failover chain → deepseek backup) | per model-selection rule |
| 3 | Topic flow review | LLM review of overlapping topics | per model-selection rule |
| 4 | Cross-session topic graph | incremental, index-first, O(N) per session | embeddings local |
| 5 | Reflection packet | mechanical aggregation; anti-pollution interface | none (invariant) |

## Bundle DAG (authoring order = topological order)

```
transcript-chunking (standalone)
      │
      ▼
annotation-store
      │
      ├────────────► uncertainty-pass ──┐
      └────────────► topic-transition-pass ──► topic-flow-review ──► topic-graph
                                                    │                      │
                                                    └──────────────────────┴──► reflection-packet
```

| # | Bundle | Boundary (in scope) | Key invariants (must) |
|---|---|---|---|
| 1 | `transcript-chunking` | render archived transcripts → canonical turn records + chunk map; deterministic; fidelity tagging | turn-integrity, chunk-coverage, deterministic-render, no-network, no-model-calls, archive-read-only |
| 2 | `annotation-store` | append/query/render sidecar annotations keyed to (file, event, chunk); append-only supersede semantics | append-only, supersede-visibility, address-integrity, archive-immutability, no-network |
| 3 | `uncertainty-pass` | LLM-free density + positional grammar + dialogue contention annotations; per-model baselines | deterministic, no-model-calls, no-network, lexicon-pinned |
| 4 | `topic-transition-pass` | topic spans w/ supporting quotes, transition events, intra-session label consistency | label-stability-across-chunks, span-in-bounds, cost-budget (should) |
| 5 | `topic-flow-review` | review overlapping/multi-topic sessions; produce topic flow report | review-consumes-all-topic-annotations |
| 6 | `topic-graph` | cross-session incremental graph (nodes=topics, typed edges, revival links); index-first O(N) | incremental-add, edge-typing, index-size-logged, embedding-local |
| 7 | `reflection-packet` | distilled meta-analysis packet consumed by reflection/retrospective/case-study/skill-review agents | derived-only (no new facts), provenance-complete, no-raw-transcript-beyond-quotes |

Note: bundles 5-6 order — topic-flow-review is intra-session (per session), topic-graph
is cross-session; topic-graph depends on topic annotations only, so both can build on
4; the flow review feeds case-study candidates into the packet.

## Cross-cutting decisions (protocol-level)

1. **The archive is immutable** → all annotations live in the sidecar overlay
   (`annotation-store`); "stitching into the transcript" happens at render time
   (annotated-transcript artifact), never in the archive.
2. **Passes are stateless batch transformations.** No pass carries long-term memory;
   the reflection packet is the only interface to the memory plane (anti-pollution
   boundary). Model selection per pass (free-failover vs deepseek) is implementation
   configuration, not protocol — protocols constrain *capability* (network to the
   router only, call budgets), not which model.
3. **Index-first topic graph.** Relationships are computed at topic-completion time,
   O(N) per new session; batch recompute (O(N²) per run) rejected. Index size is
   logged as an observable (the sqlite-vec migration trigger is a documented
   threshold check, not an estimate).
4. **Cheap-first discipline.** Passes 0, 1, 5 are LLM-free by invariant (zero-cost);
   only topic/transition and flow review call models, under the free-failover rule
   with the paid backup as last resort.
5. **Per-model baselines are calibration, not annotation.** The uncertainty pass
   emits raw densities; baselines (per model, per version, per session type) live in
   the meta layer, and deviation-from-baseline is what gets annotated.
6. **Fidelity tagging.** Every transcript is tagged `full`/`lossy` at chunking;
   density statistics must never mix fidelity classes without the tag (the
   LCB-vs-SWE artifact trap).

## Authoring order and status

| Iteration | Bundles | Status |
|---|---|---|
| This iteration | 1 `transcript-chunking`, 2 `annotation-store` | draft, linted |
| Next | 3 `uncertainty-pass`, 4 `topic-transition-pass` | planned |
| After boundary review | 5 `topic-flow-review`, 6 `topic-graph`, 7 `reflection-packet` | planned |

## Open questions (feed ambiguity logs)

- kimi wire fidelity class: verify kimi compaction behavior before sealing bundle 1.
- Should `annotation-store` enforce per-layer payload schemas (registered by passes)?
  Candidate for v0.2.
- Where the store physically lives (filesystem dir vs sqlite) — implementation
  freedom, capability manifest constrains paths only.
- Chunk-size defaults: `target_chars: 80000`, `boundary_overlap_turns: 1` — sized for
  the cheapest capable model tier; may be tuned per pass without protocol change
  (parameters are explicit).
