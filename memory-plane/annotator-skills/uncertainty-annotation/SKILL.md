---
name: uncertainty-annotation
description: Correct practice for marker/uncertainty density annotation in the transcript-annotation pipeline (word-boundary matching, archive-append dedup, auditable counts).
scope: internal (transcript-annotation pipeline only) — NOT a canonical skill; do NOT sync to harnesses.
---

# uncertainty-annotation

Internal operating skill for the transcript-annotation pipeline's deterministic
passes (`transcript-chunking`, `uncertainty-pass`). These passes are **scripts,
not skill-capable agents**; this skill encodes the standard they must satisfy.
It is **scoped**: it must never be propagated into the canonical skill store or
the general skill-sync pipeline.

## When to use
Any change to the marker-density / uncertainty / contention annotation path, or
any QC review of an `uncertainty`/`contention` annotation layer.

## Rules

### 1. Word-boundary marker matching (no substring false positives)
Markers must match only as **whole words/phrases**. A marker `M` matches at a
position iff:
- the text there equals `M` (case-folded), AND
- the char before `M`'s first word is non-alphanumeric (or text start), AND
- if `M` ends in a word char, the char after is non-alphanumeric (or text end).

This is what makes `plan` NOT match inside `executing-plans`/`plans`/`explanation`
and `but` NOT match inside `about`/`button`. Markers ending in punctuation
(`,`, `:` …) drop the trailing check. Bare substring matching is forbidden.

### 2. Collapse archive-append duplication before scanning
Full-fidelity archives (e.g. reasonix) are append-only and re-emit the cumulative
message list on each event, so the same turn appears multiple times. The renderer
**must** dedup exact-duplicate turns (same role + same normalized content),
keeping the first occurrence in order — otherwise marker counts and densities are
inflated (roughly doubled) and `turn_count` overstates the real conversation.

### 3. Auditable counts
`marker_counts` must reconcile to the text actually scanned. The scan scope
(assistant dialogue vs reasoning content), the denominator (per-1k of which text),
and the dedup state must be transparent so a reviewer can reproduce the numbers.
If a consumer needs dialogue-only uncertainty, the annotation must expose whether
each marker came from reasoning or dialogue rather than only a merged count.

### 4. Known limitation — no per-model / per-harness calibration (do NOT silently conflate)
The lexicon is a single global English heuristic. Matching behavior **will differ
across models** (different self-doubt dialects) **and across harnesses** (different
formats/rendering). Densities are therefore NOT directly comparable across
transcripts from different models or harnesses unless normalized against per-model /
per-harness baselines (the design's `baseline_refs`). Do not claim a numeric
uncertainty difference between two transcripts from different sources is signal
without calibration.

## Check
Before shipping an annotation change: no substring false positives in a fixture
(`executing-plans`, `about`, `plans`, `unfailed`), duplicate archive blocks produce
single counts, and the tests mirror these cases.
