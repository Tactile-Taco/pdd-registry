# Memory Plane Design — Letta agent fleet for skill improvement

Authoritative design for the memory plane / skill-review system. This is the consumer
side of the transcript-annotation pipeline (see `transcript-annotation-program.md` for
the 7 bundles). It describes how a fleet of Letta agents digests reflection packets to
improve skills. This document is a design; nothing here is code.

## Two processes

- **Temporary / backlog process** — a one-shot kickstart over the historical corpus.
  See `backlog-execution-plan.md`.
- **Primary process** — the ongoing system described below.

## Agent fleet (4 Letta agents)

| # | Agent | Trigger | Outputs |
|---|---|---|---|
| 1 | **Case-study curator** | skill-usage ∩ hot-patch (single session qualifies) | case studies: goal + progress + friction |
| 2 | **Reflection agent** | ≥5 days AND ≥5 MB since last, or a concluded long-running topic | reflections + skill proposals |
| 3 | **Retrospective agent** | annotation-derived checkpoints (major cluster concludes, aggregated heatmap anomaly, ~25 MB floor) | retrospectives + skill proposals |
| 4 | **Meta-agent** | cadence-based (per reflection cycle / skill-push accumulation) | system memories + fleet process-skill updates |

Agents may occasionally **write new skills** when a workflow is lacking. There is no
separate "skill-dreaming" job — the digesting agents already operate on skills as
their workspace; skill improvement happens inside reflections/retrospectives.

## Triggers (all annotation-derived)

- **skill-usage** (NEW pipeline signal, see flags): which skills a topic span was
  using, matched against the canonical skill list. Mechanical (regex on tool-call /
  slash-command / run_skill events) with optional LLM refinement.
- **hot-patch**: heatmap cells exceeding z > 1.5 (deviation-from-baseline) sustained
  over contiguous chunks — a hot region, not a single spike.
- **cluster lifecycle**: `active` / `dormant` (no new member ≥14 days) / `concluded`
  (dormant + explicitly closed by a flow-review). NEW signal.
- **Case study**: skill-usage ∩ hot-patch in the same span. Deliberately NOT
  cluster-triggered (a single session qualifies).
- **Reflection**: (≥5 days since last) AND (≥5 MB new data), OR a long-running topic
  reaches a conclusive end point.
- **Retrospective checkpoints**: a major cluster concludes (default ≥6 sessions),
  an aggregated-heatmap anomaly, or a ~25 MB cadence floor.

## Provenance model (three-way graph)

```
case study / retrospective / reflection
      │  evidence links                              │  influenced_skills (reverse)
      ▼                                              ▼
transcripts + annotations + packets      skills (edits + new skills)
                                            │
                                            └──►  section-level references ("where it had impact")
```

1. **Artifact → evidence**: provenance edges to the transcripts, annotation records,
   and packets that informed it.
2. **Skill → artifacts**: references at the impacted sections + a `## Provenance`
   section (artifact id, type, one-line impact). **Hybrid packaging**: the concise
   *why* travels with the skill; the full artifact stays in the knowledge store.
3. **Artifact → skills**: `influenced_skills` reverse index, updated when a skill
   references it.

## Skill management

- **Proposal → peer review → auto-push**: skill proposals are reviewed by the other
  fleet agents; on unanimous approval they are auto-pushed to the skills repo. No
  human gate. Git is the audit trail.
- **Grounding rule**: any skill edit or new skill must cite the artifact(s) that
  motivated it (`## Provenance`); a skill with no motivating artifact is suspect.
- **Mandatory frontmatter** (name + description) on new skills, per the canonicalizer
  pipeline.
- **Process skills** (the fleet's own procedures, incl. meta-agent updates) live in
  **Letta memory, NOT the canonical repo** — they must not sync to other harnesses.

## Memory

Memory-system design is **deferred**. The only standing constraints:
- Durable storage exists so old/forgotten topics can resurface unexpectedly.
- Anti-pollution: agents consume distilled packets, and store patterns / principles /
  recurring frictions, not per-session trivia. (Letta has settings governing this;
  evaluate when the memory system is designed.)

## Knowledge artifact store

Stores case studies, retrospectives, reflections — each with provenance edges
(artifact ↔ session/annotation, artifact ↔ skill). Shape: **sqlite relational store
for artifacts + evidence edges, with an optional embedding index** at the vector
threshold (the same sqlite-vec pattern as the topic-graph). CORTEX-AI-SUPER-RAG is
rejected (a chat UI, no persistence); the light store is built instead.

## Topic flow representation

| Layer | Source | Represents |
|---|---|---|
| Transition events | topic-transition-pass | typed topic changes (contiguous/revival/overlap/nested) |
| Intra-session flow | topic-flow-review → packet `topic_flow` | narrative + relation-typed edges |
| Cross-session flow | topic-graph | clusters + typed edges + lifecycle |
| Topic-intensity over time | heatmap row | topic intensity across chunks |

Gap (see flags): no renderable flow diagram. Add `topic_flow.flow_graph` (nodes +
typed directed edges) + a text/HTML render to the packet, as a sibling of the heatmap.

## Model / cost

- Agents follow the model-selection rule (free-failover chain → deepseek-v4-flash
  backup; deepseek for hard synthesis). Model choice is config, not invariant.
- Pipeline passes carry the ≤ $1/day cap (per program decision). Agent spend draws
  from the Bifrost budget as a separate line.

## Implementation flags for the pipeline fork (both docs reference these)

1. **skill-usage annotation layer** — new signal; needs the canonical skill list as
   reference vocabulary. (New pass or v0.2 extension of topic-transition-pass.)
2. **hot-patch anomaly detection** in the heatmap — contiguous cells at z > 1.5.
3. **cluster lifecycle** (`active`/`dormant`/`concluded`) + `concluded_clusters` in
   the reflection packet.
4. **topic_flow.flow_graph** + text/HTML render — schema extension to
   topic-flow-review response and reflection-packet packet.
5. **canonicalize.mjs must preserve the `## Provenance` section verbatim** — it is
   content, not harness-specific noise.
6. **Idempotent, resumable-by-file passes + a checkpoint journal** — required for the
   backlog runner (see backlog-execution-plan.md).
