# Ambiguity Log — annotation-store

## Resolved Assumptions

- **The envelope is content-agnostic.** The store does not interpret `payload`; each
  pass defines its own payload schema. Per-layer payload validation is deferred (see
  open questions). The store's contract is addressing, provenance, revision, and
  visibility — not annotation semantics.
- **Supersede key** is (pass_id, layer, kind, source, filename, event_id, chunk_id).
  `kind` is part of the key so different kinds in the same layer coexist (e.g. a
  density stat and a span marker in the uncertainty layer). A correction is a *new*
  record (new annotation_id, strictly higher revision) sharing the key.
- **Addressing is per-file.** Targets always carry source + filename and at least one
  of event_id / chunk_id (schema anyOf). Cross-file and cross-session queries are out
  of scope (topic-graph and reflection-packet bundles consume per-file outputs and
  build their own indexes).
- **Stitching is a derived artifact.** The render handshake outputs stitched text
  with inline markers; it is never written back to the archive (archive-immutability)
  and never persisted as a store record (renders are reproducible from store state —
  invariant deterministic-render).
- **Revision starts at 1** for the first visible record of a key; the store does not
  auto-bump revisions — passes own revision semantics (re-run with same revision =
  no-op visibility-wise; higher revision = supersede). Passes SHOULD use
  pass_version + revision so a full re-run supersedes cleanly.
- **marker_style** defaults to `bracketed` ([KIND: label]); `explicit` delegates to
  pass-defined marker text in the payload.

## Open Questions

- **Per-layer payload schemas**: should the store enforce schemas registered by
  passes (layer → schema)? Candidate for v0.2; would tighten pass/store contracts at
  the cost of store-side knowledge of pass internals.
- **Physical store location** (filesystem dir vs sqlite): implementation freedom;
  capability manifest constrains paths, not engine.
- **Batch atomicity semantics**: a multi-record append must be all-or-nothing
  (invariant bounded-batch); whether cross-batch transactionality is needed depends
  on the runner, deferred.

## Rejected Interpretations

- **Editing the archive in place** (stitching markers into archived transcripts) —
  violates the archive's immutability/append-only security model; render-time
  stitching is the accepted path.
- **Mutable update semantics** (PUT-style overwrite) — rejected in favor of
  append-only + supersede so the store remains auditable and re-runs are safe.
- **Deleting records** — rejected; superseded records stay for audit (only visibility
  changes).
- **Store-initiated network** (e.g. embedding calls inside the store) — rejected;
  passes do their own retrieval/embedding upstream; the store stays offline
  (invariant no-network).
