---
name: pdd-contract-negotiator
description: "Reconcile interdependent PDD bundles before sealing: dependency graph, handshake compatibility, conflict minuting."
---

# PDD Contract Negotiator

## Role
Implements the paper's Contract Negotiation step: dependency resolution, compatibility checking, capability reconciliation, and conflict detection across transitive protocol boundaries. Output = a sealed bundle set + negotiation minutes.

## Registry integration
- Build the DAG from the live catalog (`pdd registry index`):
  `depends_on`/`provides` across ALL bundles, not just the pair under
  negotiation. Standalone bundles seal first; dependents wait for their
  leaves (pdd-registry-client DECIDE item 0).
- Prefer existing sealed versions for dependencies — a dependency that
  already admits needs no re-negotiation; only a semantic change justifies a
  new version (DECIDE item 6).
- Minute the registry search: which candidates were searched, near-misses,
  and why the chosen handshake won. Minutes are negotiation evidence
  artifacts, not prose.

## Inputs
- Two or more `draft` bundles produced by pdd-protocol-author agents.
- Each bundle's `protocol.yaml` `depends_on` / `provides` / `consumes` declarations.

## Workflow
1. **Build the dependency graph.** Nodes = protocols; edges = `depends_on`. Reject cycles unless explicitly versioned.
2. **Handshake compatibility matrix.** For every edge A -> B: every schema A consumes from B must exist in B's `provides` and be compatible (required fields present, types equal or coercible, enums supersets of consumed values).
3. **Capability reconciliation.** If A's operational invariants assume B performs an action (e.g. "B stores durably"), B's capability manifest must permit it. Flag authority gaps (A needs B to write; B forbids writes) and authority excesses (B claims network egress nobody consumes).
4. **Behavioral cross-checks.** Detect severity conflicts (A treats B's `should` as `must`), semantic collisions (two protocols defining the same term differently, e.g. two different scoring formulas), and cross-boundary ordering assumptions lacking an invariant.
5. **Conflict classes and resolution.** Record each conflict as `type-mismatch | capability-gap | severity-conflict | semantic-collision | ordering-assumption`. Resolve by editing exactly one side (prefer strengthening the provider over weakening the consumer) or escalate to the orchestrator. Never resolve silently.
6. **Seal.** When zero open conflicts remain: bump all bundles to `status: sealed`, pin versions, emit `negotiation-minutes.md` with the matrix, conflicts, resolutions, and version pins.

## Rules
- A bundle with an open `must`-level conflict must not be sealed.
- Sealed bundles change only via explicit version events and renewed negotiation.
- The negotiator never authors invariants; it routes changes back to the owning author.
- Run `scripts/check_compatibility.py <protocols-dir>` after every change; it must pass before sealing.

## Outputs
- `negotiation-minutes.md` (matrix + conflicts + resolutions)
- sealed bundle set with pinned versions
- `compatibility-report.json` (machine-readable, consumed by CI)
