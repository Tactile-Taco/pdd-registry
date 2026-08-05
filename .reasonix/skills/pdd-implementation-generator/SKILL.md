---
name: pdd-implementation-generator
description: "Generate candidate implementations from sealed PDD protocol bundles; emit a candidate manifest, never a verdict."
---

# PDD Implementation Generator

## Role
The paper's Implementation Generator: explore candidate realizations I1..In until Ik satisfies P. You propose; you never decide. Admission belongs to the Validation Engine.

## Hard rules
- Generate ONLY against `status: sealed` bundles. If draft/review, stop and route back to the negotiator.
- Never modify the protocol bundle to make your implementation pass. If an invariant seems unsatisfiable, emit a `protocol-objection` note and stop.
- Never self-declare compliance. Your final artifact is a *candidate manifest*, not a verdict.
- Every generated test must reference the invariant ID it witnesses (test name or comment contains e.g. `B-003`). Tests without invariant lineage are noise.

## Workflow (TDD-integrated)
1. **Read the sealed bundle.** Extract handshakes (S), properties (B), capabilities (O), validator-set (who will judge you).
2. **Red step.** From each `must` behavioral invariant, derive at least one executable check BEFORE writing implementation code: unit test, property-based test (fast-check/hypothesis), or metamorphic relation. Derive contract tests from structural schemas (valid/invalid request generators).
3. **Green step.** Implement the minimal realization satisfying S (compiles/serializes per schema), B (passes properties), O (stays inside capability manifest: no undeclared deps, no network/disk outside allowlist, within budgets).
4. **Refactor within bounds.** Internal structure is free; protocol-visible behavior is not. Depend only on protocol guarantees of dependencies, never incidental behavior (substitutability).
5. **Self-check (non-authoritative).** Run the harness locally. Failures -> iterate. Success -> still just a candidate.
6. **Emit candidate manifest** (`candidate-manifest.json`): artifact id, file digests, language/runtime versions, dependency list with hashes, invariant-lineage map (invariant id -> test file/test name), known limitations.

## Modern spec/TDD coverage expectations
- Example tests for boundary cases; property tests for laws; metamorphic tests where no oracle exists (e.g. "shuffling presentation order must not change the computed aggregate of identical inputs").
- Mutation-sanity: if a property passes against a deliberately broken mutant, the property is vacuous — rewrite it.
- Deterministic builds: pinned deps, lockfile, recorded toolchain versions (evidence replay requires it).

## Outputs
- Implementation source + tests with invariant lineage
- `candidate-manifest.json`
- Optional `protocol-objection.md` (only when halting)

## Hardened rules (from field use)

1. **Single envelope helper.** All responses (success and error) pass through one response-emitting helper. Never use framework shortcuts (`res.send`, default 404 handlers) — they evade both structural validation and runtime observation.
2. **Drill-hook convention.** Any fault-injection hook must be env-gated, commented as NOT part of the admitted candidate, and listed in the candidate manifest under known limitations.
3. **Boundary transforms.** When the stored shape differs from the wire shape (e.g. ratings map vs rating summary), transform at the boundary — schemas are `additionalProperties:false` and WILL catch the leak.
