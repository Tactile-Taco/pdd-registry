# Future plans & known gaps

Ideas and gaps that were discussed and deliberately deferred. Each file is a
sketch — a direction, not a commitment. Status legend: 🔮 sketch | 🧭 scoped |
⏸️ parked.

| Plan / gap | Status | Summary |
|---|---|---|
| [mcp-token-mint.md](mcp-token-mint.md) | 🧭 | **Partially landed (2026-08-10, MCP Phase B)**: admin-gated mint/revoke + hash-at-rest + audit trail + minted-token publish authn are live in pdd-registry-mcp 1.1.0; remaining: rotation policy + revocation UX polish |
| [validator-attestation-for-registry-entries.md](validator-attestation-for-registry-entries.md) | 🧭 | **Partially landed (2026-08-10)**: `taxonomy/validator-receipt` bundle + registry receipt observation (author receipts inside signed_object are parsed per the taxonomy and reported in /evidence/verify); remaining: replay-on-demand + registry-enforced receipts |
| [taxonomy-service.md](taxonomy-service.md) | 🧭 | **Partially landed (2026-08-10)**: sealed taxonomy bundles `taxonomy/web-service` + `taxonomy/ai-agent` (vocabulary in capabilities.components, should-tier templates, depends_on composition) are live catalog entries — discover via `?tag=taxonomy`; remaining: resolver endpoint + conformance tooling |
| [language-agnostic-candidate-harness.md](language-agnostic-candidate-harness.md) | ✅ | **Landed (2026-08-10)**: manifest `test_command` + language gates; demo `shell-stdlib` candidate for taxonomy-web-service validated (admit); remaining: non-Python runtimes on the validator host / sandbox image |
