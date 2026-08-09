# Validator attestation for registry entries (sketch)

**Context.** S-007 (pdd-registry v1.2+) makes validation author-owned: the
registry stores signed evidence whose `resource_identifier` points at the
author's validator-loop execution record. The registry does not re-run
validation and does not prove it (honor system). The user wants to keep the
repo/validator-harness agnostic — authors may use any validator.

**Question to explore.** How can a registry entry carry *attestation* that
the validator loop actually performed as expected, without the registry
dictating a harness?

**Ideas.**
- **Attestation envelope**: the author's signed evidence already pins
  validator identities + versions (`validators` list) and results. A
  future invariant could require the evidence to include a
  *validator-execution receipt* (e.g. the CI run's conclusion + artifact
  digests) that third parties can re-check without trusting the author.
- **Replayable results**: `evidence_chain.py replay` already recomputes
  admission from preserved inputs — a registry endpoint could offer
  replay-on-demand for any entry, letting a verifier re-run the digest
  chain deterministically.
- **Cross-harness portability**: the evidence `runtime`/`os`/validator
  fields are harness-agnostic today; a taxonomy of validator-run receipt
  shapes (GitHub Actions run, other CI, local attestation) would let the
  registry *parse* receipts rather than just store a URL.

**Why deferred.** S-007's honor system is a deliberate v1.x trade; this
exploration changes the trust model and belongs with the taxonomy work and
any non-Python validator harness (see `language-agnostic-candidate-harness.md`).
