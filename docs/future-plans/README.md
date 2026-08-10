# Future plans & known gaps

Ideas and gaps that were discussed and deliberately deferred. Each file is a
sketch — a direction, not a commitment. Status legend: 🔮 sketch | 🧭 scoped |
⏸️ parked.

| Plan / gap | Status | Summary |
|---|---|---|
| [mcp-token-mint.md](mcp-token-mint.md) | 🧭 | **Partially landed (2026-08-10, MCP Phase B)**: admin-gated mint/revoke + hash-at-rest + audit trail + minted-token publish authn are live in pdd-registry-mcp 1.1.0; remaining: rotation policy + revocation UX polish |
| [validator-attestation-for-registry-entries.md](validator-attestation-for-registry-entries.md) | 🔮 | Evolve the S-007 honor system: registry entries carry validator attestation beyond resource_identifier, while staying repo/validator-harness agnostic |
| [taxonomy-service.md](taxonomy-service.md) | 🧭 | **Partially landed (2026-08-10)**: sealed taxonomy bundles `taxonomy/web-service` + `taxonomy/ai-agent` (vocabulary in capabilities.components, should-tier templates, depends_on composition) are live catalog entries — discover via `?tag=taxonomy`; remaining: resolver endpoint + conformance tooling |
| [language-agnostic-candidate-harness.md](language-agnostic-candidate-harness.md) | 🔮 | Make the candidate surface language/framework-agnostic (manifest `test_command`, execution envelope) so non-Python candidates can be validated |
