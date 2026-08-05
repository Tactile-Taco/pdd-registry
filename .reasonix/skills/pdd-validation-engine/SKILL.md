---
name: pdd-validation-engine
description: "Run the PDD Validator Loop: structural/behavioral/operational checks, property tests, mutation sanity, verdict."
---

# PDD Validation Engine

## Role
The admission controller. Generation proposes; validation decides. All three layers are jointly necessary: structural checks miss semantics, behavioral checks miss hidden capabilities, operational checks cannot establish meaning.

## Layer 1 — Structural (S)
- Compile/serialize check; validate payloads against bundle JSON Schemas (ajv or equivalent).
- Contract tests: enumerate required fields, nullability, enums, error variants; probe with valid + invalid generators.
- Version compatibility: minor versions add optional fields only.

## Layer 2 — Behavioral (B)
- Property-based tests for every `must` invariant with a `property` statement (fast-check; >=200 cases default, >=5000 nightly).
- Metamorphic relations where oracles are absent.
- Regression suite: every historical runtime violation block gets a corresponding test before re-admission.
- Mutation sanity: at least one hand-built mutant per core property must FAIL the property; otherwise flag the property `mutation-suspect`.
- Coverage gates: line >=80%, branch >=70% on protocol-critical modules; MCDC-style per-condition analysis on admission-critical predicates (document each condition's independent effect).

## Layer 3 — Operational (O)
- Dependency scan: package manifest + import graph vs capability allowlist.
- Sandbox/egress monitor: non-allowlisted network/filesystem/secret access = violation.
- Resource measurement: latency p95, peak RSS, concurrency under declared workload vs budgets.
- Background-work detection: timers, detached promises, unsubscribed observers.

## Verdict format
Emit `validation-results.json`:
```json
{ "protocol": {"name": "...", "version": "...", "bundle_digest": "sha256:..."},
  "candidate_digest": "sha256:...",
  "validators": [{"id": "...", "version": "...", "layer": "structural|behavioral|operational"}],
  "results": [{"invariant_id": "...", "layer": "...", "outcome": "pass|fail|skip|mutation-suspect", "evidence": "..."}],
  "verdict": "admit | reject",
  "verdict_reason": "..." }
```
- `admit` iff every `must` invariant across all layers passes and no `mutation-suspect` flags are open.
- On reject: include minimal (shrunk) counterexamples — they become remediation context.

## Rules
- Validators must appear in the bundle's validator-set (identity + version). Unknown validator = invalid run.
- Never edit candidate code to make it pass. Reject and route to remediation.
- Preserve all raw outputs under the evidence namespace for replay.

## Performance and infrastructure contingency (project policy)

1. **Performance budgets are advisory by default.** Latency/memory/CPU invariants with `severity: should` are measured and recorded in the evidence results; observed excess is reported as an observation, NOT a rejection. Only `must`-severity capability invariants (network, filesystem, secrets, dependencies, background work) hard-fail admission.
2. **Harness mode when infra is absent.** If the deployment/RVL infra named by the bundle's `infra_assumption` does not exist, validate operationally in harness mode: sandboxed execution (docker `--network none --read-only` when docker is available; otherwise local subprocess with environment stubs), dependency scan, and resource measurement. Record the mode in the evidence results.
3. **Never let missing infrastructure masquerade as a pass.** A validator that could not actually enforce a capability invariant must mark that invariant `skip` (with reason), not `pass`.

## Hardened rules (from field use)

1. **Uniqueness budgets for generators.** When a property needs distinct entities (usernames, ids), draw from a space at least 1e6x the run count, dedupe, or use unique generators. Small spaces + many runs = birthday collisions = flaky validators.
2. **Isolate aggregate assertions.** Any assertion over global/aggregate state (rankings, totals, counts) runs against a dedicated fresh fixture, or is expressed relative to measured prior state — never against a fixture shared with randomized earlier properties.
3. **Probe the unknown.** Structural suites must include negative-space probes (unknown routes, unknown methods) — endpoint inventories go stale the day a framework default handler appears.
4. **Choke-point check for HTTP protocols.** Operational scans should reject response paths that bypass the envelope helper (`res.send`, `writeHead`, framework defaults) outside the RVL itself.
5. **Mutation sanity is non-optional.** A property that passes against a deliberate mutant is evidence against itself; mark it `mutation-suspect` and do not admit until rewritten.
