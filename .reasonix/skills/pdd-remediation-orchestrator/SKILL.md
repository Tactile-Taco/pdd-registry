---
name: pdd-remediation-orchestrator
description: "Close the PDD remediation loop: violation to repair context, candidate re-validated, outcome recorded on the ledger."
---

# PDD Remediation Orchestrator

## Role
Owns the closed loop: violation block -> repair context C_t -> regenerated candidate I' -> Validate(I', P) -> new ledger block. A patch generated in response to a failure is NOT trusted because it responded to a real failure; it re-enters admission like any candidate.

## Registry integration
- Route by classification against the registry (pdd-registry-client DECIDE):
  `implementation-defect` → updated implementation + NEW evidence chain
  (item 5); `protocol-gap` → NEW VERSION of the protocol (item 6);
  `validator-defect` → validator fix + re-validation (new results, same
  chain); `environment-drift` → re-run with corrected provenance.
- The repair candidate I' re-enters admission like any candidate — same
  engine, same gates; the violation block's ledger head binds the repair to
  the exact registry state (protocol version + impl digest) it responded to.
- DAG-aware: remediating a leaf unblocks its dependents; remediating a
  dependent before its leaves admit is a no-op.

## Workflow
1. **Ingest violation.** Either (a) runtime `attest-violation` block digest from the RVL, or (b) build-time `reject` verdict from the Validation Engine.
2. **Build repair context C_t**: violated invariant id + statement; layer (S/B/O); shrunk counterexample or redacted observation; protocol version + bundle digest; implementation version + artifact digest; environment metadata; ledger head digest; recurrence count for this invariant (ledger scan); classification `implementation-defect | protocol-gap | validator-defect | environment-drift`.
3. **Route by classification.**
   - `implementation-defect` -> Implementation Generator with C_t.
   - `protocol-gap` -> Protocol Author via orchestrator; requires version event + renewed negotiation; never patch a sealed bundle in place.
   - `validator-defect` -> Validation Engine maintainer; quarantine the validator version in the validator-set.
   - `environment-drift` -> CI architect / human; record dependency or toolchain change.
4. **Gate re-admission.** I' must pass the full Validator Loop AND a new regression test derived from C_t. No hot-patching.
5. **Record outcome.** Append `remediation-outcome` block: {original violation digest, repair context digest, new candidate digest, new verdict, regression test id}.
6. **Recurrence watch.** Same invariant failing >=3 times across versions -> escalate: the protocol itself may be mis-authored.

## Classification policy (project policy)

- **Performance excess on `should` invariants -> `environment-drift`.** Latency/memory budget excess under provisional infrastructure is environment drift by default, not an implementation defect. Only a `must`-severity capability violation (egress, filesystem, secrets, dependency use) is an `implementation-defect` by default.
- **Missing infra -> `environment-drift`.** If the bundle's `infra_assumption` names infrastructure that does not exist, the gap is recorded as environment drift (CI/human route), never as an implementation defect.

## Rules
- Never let a generator self-approve a repair.
- Never delete or edit violation history; remediation is append-only.
- Every remediation terminates in a new admission (with evidence) or an open incident assigned to a human.
