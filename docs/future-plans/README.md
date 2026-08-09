# Future plans & known gaps

Ideas and gaps that were discussed and deliberately deferred. Each file is a
sketch — a direction, not a commitment. Status legend: 🔮 sketch | 🧭 scoped |
⏸️ parked.

| Plan / gap | Status | Summary |
|---|---|---|
| [mcp-token-mint.md](mcp-token-mint.md) | 🔮 | Per-agent publish tokens (admin-gated mint with audit) + durable Infisical↔cluster token sync; closes the onboarding gap (see `docs/onboarding.md`) |
| [validator-attestation-for-registry-entries.md](validator-attestation-for-registry-entries.md) | 🔮 | Evolve the S-007 honor system: registry entries carry validator attestation beyond resource_identifier, while staying repo/validator-harness agnostic |
| [taxonomy-service.md](taxonomy-service.md) | 🔮 | Registry entries that define protocol-bundle taxonomies (vocabulary + invariant templates) for common architectures/systems, composed via `depends_on` |
| [language-agnostic-candidate-harness.md](language-agnostic-candidate-harness.md) | 🔮 | Make the candidate surface language/framework-agnostic (manifest `test_command`, execution envelope) so non-Python candidates can be validated |
