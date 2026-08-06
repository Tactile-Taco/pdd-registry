# Registry Server Proposal — from minimal-service v1 evidence

Status: proposal · Based on: docs/service-features-v1.md + the verified
minimal service (M1–M3 of the deploy goal) + local usage.

## What v1 proved

The minimal service's endpoints — /bundles, /evidence/verify,
/evidence/admission — are **all read-only** and were the ones developers
reached for. Nothing in v1 usage suggests anyone wants to *push* bundles over
HTTP: distribution via git + CI already works, evidence is append-only by
design, and the k3s deploy pipeline moves artifacts.

## Decision: the registry server is a READ API + SEARCH, not a push/pull host

- **Git remains the distribution layer** (bundles, implementations, evidence
  live in the repo; the container *is* the repo). This is a deliberate
  difference from Docker Hub: PDD artifacts are small, human-readable, and
  already versioned by git — a push/pull registry would duplicate that
  machinery for no gain.
- **The server adds what git can't**: structured, low-latency queries over the
  registry catalog.

## Proposed v2 surface (incremental, all read-only)

| Endpoint | Adds over v1 | Why it's wanted |
|---|---|---|
| `/search?q=idempotent` | text search over bundle names + invariant statements | find "who guarantees idempotency" |
| `/bundles/{name}/invariants?severity=must` | structured invariant view (S/B/O) | audit a bundle without cloning |
| `/bundles/{name}/capabilities` | capability manifest view | "which bundles forbid network?" |
| `/bundles?status=sealed&depends_on=X` | filtered index incl. dependency graph | negotiate new protocols |
| `/evidence/{name}/ledger?limit=N` | ledger view | runtime attestation history |
| `/diff?bundle=A@v1&bundle=A@v2` | schema/invariant diff between versions | version-event review (S-003) |

All JSON, still tailnet-only in staging; **auth is the one genuinely open
question** — it becomes necessary only if the registry is exposed beyond the
tailnet or serves multiple teams.

## What this means for the repo

- The minimal service (`src/server.py`) already has the data accessors; v2 is
  additive: a search index (in-memory over pdd-bundles/* on startup) + the new
  routes. No schema/evidence changes — the evidence chain is untouched.
- The self-hosted runner + `pdd-staging-deploy.yml` (docs/runner-proposal.md)
  make the service deploy automatically on push to dev; the registry server
  then rides the same pipeline.

## Verdict

Build v2 as a read API + search, on top of the existing minimal service, when
developers hit the first gap the current four endpoints can't answer. Do NOT
build push/pull or multi-user auth unless a concrete multi-team/public use
case appears — the evidence so far argues against it.
