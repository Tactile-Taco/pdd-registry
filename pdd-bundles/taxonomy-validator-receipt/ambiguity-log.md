# Ambiguity Log

- **Receipts are observations, not requirements (S-007 additive).** The
  registry reports a receipt observation when signed_object carries a
  validator_receipt; records without one are unchanged (honor system
  intact). No publish-schema change: receipts travel inside the
  author-signed signed_object, which is stored verbatim.
- **Replay boundary.** Receipts are re-CHECKABLE (shapes + digests), but
  the registry does not re-check them against the external system, and
  replay-on-demand remains deferred: DB-side replay is only meaningful
  for registry-owned canonicalization (the three-state verify work owns
  that boundary).
- **Provider additions.** New provider shapes are additive (S-003 of
  pdd-registry-mcp discipline applies to this taxonomy's own versioning):
  a new provider never breaks parsing of existing receipts.
- **`taxonomy` tag reuse.** Consistent with the other taxonomy bundles.
