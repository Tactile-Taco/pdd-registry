# Negotiation Minutes — taxonomy/validator-receipt

## v1.0.0 version event (validator attestation, additive S-007 evolution)

- **Decision (2026-08-10)**: first concrete step of the
  validator-attestation plan: a cross-harness RECEIPT taxonomy + shape
  validator, integrated as an additive observation in the registry's
  evidence verify path. Authors MAY attach a receipt (inside
  signed_object); the registry parses it per this taxonomy and reports
  validity. No requirement, no re-running, no trust claim — receipts are
  re-checkable content.
- **Version**: 1.0.0.
- **Invariants**: S-001..S-004 (should-tier templates: attachment,
  digests, shape conformance, pointer discipline) + B-001 (must: shape
  validation for the three providers) + O-001..O-004.
- **Compatibility matrix**: taxonomy/validator-receipt 1.0.0 (sealed); no
  dependents; provider shapes are additive-only going forward.
- **Enforcement**: contract tests + mutation sanity on B-001; the
  registry's _db_evidence_verify parses signed_object.validator_receipt
  with this validator (importlib from the image) and reports
  {receipt: {provider, valid, errors}} as an observation.
