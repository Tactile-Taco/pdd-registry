# Ambiguity Log — topic-transition-pass

## Resolved Assumptions

- **Model choice is configuration, not protocol.** Per the program decision, this
  bundle constrains *capability* (network to the model router only, call count,
  cost budget) but not which model/provider. The free-failover chain (free tiers
  first, deepseek-v4-flash backup) and its ordering live in the pass's runtime
  config and may change without a protocol bump.
- **Determinism is not required** (LLM output varies). Re-runs and corrections are
  handled by annotation-store supersede semantics (same pass_version/layer/target →
  highest revision visible), so evidence records `deterministic_replay_required:
  false`. The structural shape (schema, connectivity, coverage) IS deterministically
  checkable and remains `must`.
- **Label consistency is intra-session.** The pass receives `existing_labels` from
  the session's topic layer (already-stored topics) and reuses a label when a
  near-equivalent exists, rather than coining near-duplicates. Cross-session
  vocabulary unification belongs to the topic-graph bundle. Enforced by a
  `label_consistency_check` validator (similarity post-check against
  existing_labels).
- **Transition types** are enumerated as contiguous (adjacent topics), revival
  (return to an earlier topic — Grosz & Sidner topic resumption), overlap
  (concurrent/multi-topic), and nested. Multi-topic chunks are explicitly allowed
  (overlapping topics were a stated requirement).
- **Supporting quotes ground topics** (TopicGPT-style): each topic carries
  (chunk_id, event_id, text) references so labels are auditable against the render.
- **Per-topic intensity** is emitted to feed the topic-intensity heatmap row (which
  is always available from non-LLM cells too; this pass's intensity is the LLM-labeled
  variant layered on).
- **Cost accounting** is via `tokens_in`/`tokens_out` in the response + telemetry,
  feeding a cost ledger; the $1/day budget is a `should` (project policy: resource
  invariants default to should), monitored rather than admission-gating.

## Open Questions

- **Cross-session label unification** — whether topic-graph should canonicalize
  labels globally (a global label index) or keep per-session labels with similarity
  edges. Deferred to the topic-graph bundle.
- **Call-count bound** (2/chunk incl. retries) is a should; whether an oversized
  session should be sub-chunked further to bound worst-case paid spend is an
  implementation decision to confirm against the real corpus.

## Rejected Interpretations

- **Emitting topic labels only as free text** — rejected; topics must carry quote
  grounding and span for auditability and for the flow-review pass.
- **Guaranteeing determinism** — rejected; not achievable for an LLM pass, and the
  store's supersede semantics make it unnecessary.
- **Doing cross-session linking or flow review here** — rejected; those are separate
  bundles (topic-graph, topic-flow-review), keeping this pass's boundary narrow.
- **Relying on paid models as primary** — rejected; free-failover-first is the
  documented default and the cost budget constrains paid usage to the backup path.
