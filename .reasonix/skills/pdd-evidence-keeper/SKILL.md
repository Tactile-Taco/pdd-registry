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
- Registry digest-drift policy (handoff §2): the validation results file is
  truth; an old admission may attest an earlier bundle digest — keep it, do
  not re-sign history. Optional hardening (not yet built): `evidence verify`
  warning when the embedded digest differs from the live bundle.
- `runtime-ledger.jsonl` blocks identify the protocol version + impl digest
  from the registry; runtime evidence extends the chain append-only.

## Build-time: Evidence Chain
Evidence object per paper: `E = H(P, I, V, R, t)` — P protocol bundle digest, I implementation artifact digest, V validator identities+versions, R validation results, t time/environment/provenance.
- Digest: SHA-256 over canonical JSON (sorted keys), or file bytes for artifacts.
- Signing: HMAC-SHA256 with an environment key for dev; ed25519 for release gates.
- Layout (stem-keyed since the v1.1 version event so a version event
  appends a NEW object and never overwrites a prior version's attestation):
  ```
  evidence/<protocol>/
    admission/{impl[:16]}-{bundle[:12]}.evidence.json
    discovery/{impl[:16]}-{bundle[:12]}.discovery.json
    runtime-ledger.jsonl
  ```
  Pre-v1.1 objects written as `{impl[:16]}.evidence.json` are retained
  (legacy names are migrated only when they attest the current bundle
  digest; older-version objects stay in place, ledger blocks stay
  append-only).

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
- `staleness` (pdd CLI, S-008 v1.3.0) — keyless freshness gate: the latest
  admission must attest the CURRENT on-disk bundle digest; any bundle
  change without re-validate + `evidence build` fails the gate. Wired into
  `make all` + CI; run it after every bundle edit before committing.

## Rules
- No admission without an evidence object; no deployment without a genesis block.
- `verify` failure = governance incident: quarantine artifact, notify remediation.
- Freshness (S-008): never let the bundle drift from the latest
  attestation — re-validate + re-attest on every bundle change, or the
  migrated registry record silently stops being covered by evidence.
