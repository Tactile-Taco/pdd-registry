---
name: pdd-evidence-keeper
description: "Maintain PDD Evidence Chains and Dynamic Evidence Ledgers: signed evidence, Discovery Logs, hash-chained ledgers."
---

# PDD Evidence Keeper

## Role
Owns the Evidence Store. Guarantees: every admitted artifact is linked to its protocol, validators, and results by a verifiable chain; every runtime observation extends that chain append-only.

## Registry integration
- Every admitted registry artifact is chained: protocol version digest,
  implementation candidate digest, validator identities, results, t. A new
  implementation variant or a new protocol version = a NEW chain with its
  own genesis admission; an updated implementation = a superseding admission
  block on a new chain (the old chain stays as re-verifiable history).
- Registry digest-drift policy (handoff §8): the validation results file is
  truth; an old admission may attest an earlier bundle digest — keep it, do
  not re-sign history. Optional hardening (not yet built): `evidence verify`
  warning when the embedded digest differs from the live bundle.
- `runtime-ledger.jsonl` blocks identify the protocol version + impl digest
  from the registry; runtime evidence extends the chain append-only.

## Build-time: Evidence Chain
Evidence object per paper: `E = H(P, I, V, R, t)` — P protocol bundle digest, I implementation artifact digest, V validator identities+versions, R validation results, t time/environment/provenance.
- Digest: SHA-256 over canonical JSON (sorted keys), or file bytes for artifacts.
- Signing: HMAC-SHA256 with an environment key for dev; ed25519 for release gates.
- Layout:
  ```
  evidence/<protocol>/
    admission/<artifact-digest>.evidence.json
    discovery/<artifact-digest>.discovery.json
    runtime-ledger.jsonl
  ```

## Discovery Log contents
Language/compiler versions; dependency graph + package hashes; generated files + artifact digests; validator identities/versions; property coverage + outcomes; observed resource usage; derived behaviors not enumerated in the protocol (promotion candidates).

## Runtime: Dynamic Evidence Ledger
- Genesis block L0 = admission evidence object.
- Each block: `Et = H(Et-1, P, Iv, Rt, At, t)`; `At` in `attest-pass | attest-violation | remediation-outcome`.
- Append-only: corrections are new blocks.
- Violation blocks bind: protocol id+version, implementation version, violated invariant id, redacted observation, verifier identity.

## Operations (scripts/evidence_chain.py)
- `build` — construct + sign an evidence object from validation results.
- `append` — append a ledger block (recomputes chain hash).
- `verify` — re-walk a ledger, recompute every link, report first divergence.
- `replay` — recompute admission from preserved inputs and compare digests.

## Rules
- No admission without an evidence object; no deployment without a genesis block.
- `verify` failure = governance incident: quarantine artifact, notify remediation.
