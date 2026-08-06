---
name: pdd-protocol-author
description: "Convert human-language requirements into PDD protocol bundles: typed handshakes, S/B/O invariants, capability manifests."
---

# PDD Protocol Author

## Overview

Use this skill to turn human intent into a machine-checkable PDD bundle. Treat natural language as a starting point, not the final artifact: extract intent, expose ambiguity, make conservative assumptions, and emit structural, behavioral, and operational constraints.

## Registry integration
- Search the registry BEFORE authoring (pdd-registry-client DECIDE):
  `python3 scripts/pdd.py search "<capability>"`. Author a new bundle only
  when no sealed protocol covers the capability and the gap is documented in
  the ambiguity log. A near-match usually means NEW VERSION (semantic change
  to an existing protocol) or NEW IMPLEMENTATION (protocol fits, candidate
  missing) — not a new bundle.
- Declare `depends_on`/`provides` from the live catalog so the dependency
  DAG stays buildable; prefer sealed dependencies. In any multi-bundle
  program, standalone bundles (no unvalidated deps) are authored first
  (DECIDE item 0).

## Operating Mode

- If the user asks for a bundle, create files.
- If the user asks for design help, return the bundle in Markdown/YAML without writing files.
- If the requirement is too ambiguous to proceed safely, ask up to three blocking questions. Otherwise proceed with explicit assumptions and an ambiguity log.
- Default to language-neutral JSON Schema plus YAML manifests unless the user requests OpenAPI, Protobuf, TypeScript, Zod, Rego, TLA+, or another stack.

## Workflow

1. Extract intent.
   - Identify component name, purpose, inputs, outputs, state, dependencies, side effects, failure modes, users, and trust boundaries.
   - Define the protocol boundary: what is inside the component and what remains external.

2. Scan for ambiguity.
   - Flag words such as "safe", "fast", "valid", "secure", "reasonable", "retryable", "handle errors", "near real time", "best effort", and "should not call too much".
   - Resolve ambiguity by turning vague prose into explicit constraints, assumptions, or open questions.
   - Use `references/ambiguity-taxonomy.md` when the requirement has many vague phrases.

3. Draft typed handshakes.
   - Specify request, response, event, or file schemas.
   - Include required/optional fields, nullability, enums, error variants, versioning, idempotency keys, and compatibility rules.
   - Prefer small stable schemas over broad object blobs.

4. Author invariants.
   - Structural invariants (`S`): schema, interface, versioning, compatibility, serialization, and error-shape constraints.
   - Behavioral invariants (`B`): idempotence, determinism, monotonicity, ordering, conservation, authorization, failure semantics, data integrity, and state transition properties.
   - Operational invariants (`O`): network, disk, database calls, latency, memory, CPU, secrets, concurrency, dependency allowlists, logging, and background work.
   - Use `references/invariant-patterns.md` for common invariant templates.

5. Define validators and evidence.
   - Map each invariant to at least one validation method: schema validation, unit/property test, fuzzing, sandbox policy, static analysis, runtime instrumentation, provenance check, or manual review gate.
   - Specify evidence artifacts: validation logs, hashes, dependency lockfiles, sandbox traces, coverage summaries, signed attestations, and Discovery Logs.

6. Emit the PDD bundle.
   - For file output, use this structure by default:
     ```text
     pdd-bundles/<protocol-name>/
     ├── protocol.yaml
     ├── schemas/
     │   ├── request.schema.json
     │   └── response.schema.json
     ├── capability-manifest.yaml
     ├── invariants/
     │   ├── structural.yaml
     │   ├── behavioral.yaml
     │   └── operational.yaml
     ├── validators/
     │   ├── validation-plan.yaml
     │   └── validator-set.yaml
     ├── ambiguity-log.md
     └── evidence-requirements.yaml
     ```
   - Start from `assets/templates/` when creating files.
   - Run `scripts/check_bundle.py <bundle-dir>` after creating or editing a bundle (hardened linter), then `scripts/validate_pdd_bundle.py <bundle-dir>` (upstream structural check).

## Output Requirements

Every completed bundle should include:

- A short protocol purpose and boundary.
- Canonical typed handshake(s).
- At least one invariant in each class: structural, behavioral, operational.
- An ambiguity log with resolved assumptions and open questions.
- A validation plan that links invariants to validator mechanisms.
- Evidence requirements sufficient for audit and regeneration.

## Authoring Rules

- Do not invent domain facts silently. Mark assumptions.
- Prefer explicit numeric limits over vague adjectives.
- Prefer protocol-visible behavior over implementation internals.
- Keep implementation suggestions optional; PDD governs admission, not style.
- Distinguish "must" invariants from "should" preferences.
- Do not claim a property is proven unless the validator plan can establish it under stated assumptions.
- If a requirement conflicts with an operational invariant, surface the conflict before writing implementation guidance.

### Performance and infrastructure leniency (project policy)

- **Performance/resource invariants default to `severity: should`** with generous budgets (e.g. latency, memory, CPU, throughput). Upgrade to `must` only with an explicit budget rationale. The reason: infrastructure is provisional; hard performance gates would reject otherwise sound implementations on environment noise.
- **Capability invariants stay `must`** (network egress, filesystem access, secret access, dependency allowlists, background work): they are enforceable with sandboxing alone and do not depend on performance infrastructure.
- Every operational invariant may declare an `infra_assumption` field stating what the validator needs (sandbox, docker, CI runner, deployment/RVL). When the assumed infra is absent, the invariant is validated in harness mode (measurement + replay), and observed excess is recorded as an observation — not an admission failure — unless the invariant is a hard capability `must`.
- Record in `ambiguity-log.md` any invariant whose enforcement depends on infrastructure that may not exist.

## References

- `references/pdd-bundle-spec.md`: bundle file contract and field meanings.
- `references/ambiguity-taxonomy.md`: common ambiguity classes and how to resolve them (includes a fully worked critical-ambiguity case).
- `references/invariant-patterns.md`: reusable structural, behavioral, and operational invariant patterns.
- `references/examples.md`: worked examples from natural language to PDD bundle.

## Team Extensions

1. **Cross-protocol declaration.** `protocol.yaml` gains optional `depends_on` (list of protocol names this bundle consumes) and `provides` (named handshakes other bundles may reference). Never let two bundles define the same handshake differently — promote shared shapes to a `shared/` schema and reference it.
2. **Validator set registry.** Every bundle includes `validators/validator-set.yaml` listing approved validator identities and versions (paper appendix conformance). The Validation Engine must reject runs by unlisted validator versions.
3. **Runtime-ledger slot.** Bundle layout includes `evidence/runtime-ledger.jsonl` (created empty) so the Runtime Verification Layer has a canonical append target inside the evidence namespace.
4. **Hardened linter.** Run `scripts/check_bundle.py <bundle-dir>` — verifies required files, status enum, unique invariant IDs, validator mapping for every `must` invariant, resolvable handshake references, and a non-empty validator set.
5. **Mediated Q&A rule.** When authoring inside a team, you may NOT read reference implementations or external sources. Ask the orchestrator formal questions; record answers in the ambiguity log as resolved assumptions with provenance `orchestrator`.
6. **Critical ambiguity classification.** Every resolved ambiguity is classified by blast radius before sealing:
   - **Cosmetic**: all readings yield the same observable behavior. Safe to assume; log as a resolved assumption.
   - **Critical (behavior-changing)**: competing readings produce materially different observable behavior (a user or validator could tell them apart).
   Rules for critical ambiguities:
   - If a reference, orchestrator, or human can adjudicate, ASK — never silently assume a critical reading. This is a blocking question.
   - If forced to proceed anyway, record with `criticality: behavior-changing`: the competing readings, the chosen reading, the rationale, and the concrete test that would reveal a wrong choice.
   - A wrong critical reading discovered after sealing is a **protocol-gap remediation**: protocol version event, renewed negotiation, fresh evidence. Never patch a sealed invariant's text silently.
   - Heuristic for spotting them: watch nouns with positional or temporal scope ("current", "latest", "active", "completed", "valid", "previous") — they routinely hide behavior-changing readings. See the worked case in `references/ambiguity-taxonomy.md`.
