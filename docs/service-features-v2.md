# pdd Registry Server — Client-Facing Feature Enumeration v2 (draft, pre-build)

Purpose: define the registry-server iteration's client surface BEFORE building
it, per the PDD methodology (docs/service-features-v1.md was the same contract
for the minimal service; docs/registry-server-proposal.md is the strategic
decision this doc operationalizes). Version 2 of this doc is written before
any of the v2 code exists; it is the contract the v2 surface is checked
against.

## The iteration in one sentence

Turn the minimal read-only service (v1: `/healthz`, `/bundles`,
`/evidence/verify`, `/evidence/admission`) into a **registry server**: a read
API + search over the protocol catalog, so developers and agents can answer
"who guarantees X" and "which bundles constrain Y" without cloning the repo.

## Actors

1. **Developer (human)** — asks "which bundle guarantees idempotency?" or
   "which bundles forbid network access?" before authoring a new protocol.
2. **Agent (LLM/CI)** — programmatic queries for gates and negotiation
   (capability-aware protocol matching).
3. **Operator** — same read surface plus ledger history for attestation
   review; writes still happen via git + deploy (unchanged from v1).

## Decisions inherited from the proposal (not re-litigated)

- **Git remains the distribution layer.** No push/pull over HTTP.
- **No auth in v2** (tailnet-only in staging; auth only when exposure
  widens). Documented as an open question, not silently ignored.
- **Evidence chain is untouched.** v2 adds views over existing data; it
  never mutates bundles, implementations, or evidence.
- **Fail-closed honesty**: a view that cannot be produced reports an explicit
  error/`ok:false` — never fabricated data.

## Client-facing surface (v2)

### CLI (`scripts/pdd.py`) — the index the HTTP API serves

| Command | Returns | Notes |
|---|---|---|
| `pdd index` | JSON catalog: per bundle `{name, version, status, depends_on, provides, invariants:{S,B,O} counts, capabilities}` | builds the in-memory index over `pdd-bundles/*`; exit 0, or 1 with an error if a bundle is unparseable. Requires pyyaml (pinned in the Dockerfile; fail-closed without it — explicit error, never fabricated data) |
| `pdd search <q>` | JSON matches: `{query, results:[{bundle, layer, id, text, score}]}` | tokenized search over bundle name, purpose, invariant id+statement+severity, capability keys; ranked by relevance; exit 0 (≥1 match) / 1 (no match). AND semantics per entry: every query token must appear in one entry's text (so `idempotent network` finds nothing today even though both words appear somewhere in user-registry) |

### HTTP (the service) — additive over v1

| Endpoint | Method | Returns | Notes |
|---|---|---|---|
| `/search?q=idempotent` | GET | `{query, count, results:[{bundle, layer, id, text, score}]}` | same index/ranking as `pdd search`; `q` required (400 if missing/empty) |
| `/bundles?status=sealed&depends_on=X` | GET | filtered `{bundles:[{name, version, status, depends_on, provides}]}` | v1 `/bundles` becomes filterable; both filters optional, combinable |
| `/bundles/{name}` | GET | `{name, version, status, purpose, boundary, depends_on, provides, invariant_ids:{S:[…],B:[…],O:[…]}}` | full bundle summary; 404 for unknown name |
| `/bundles/{name}/invariants?severity=must` | GET | `{bundle, invariants:{structural:[…], behavioral:[…], operational:[…]}}` | each invariant `{id, statement, severity, validation}`; optional `severity` filter (must/should); 404 unknown name |
| `/bundles/{name}/capabilities` | GET | capability manifest (network, filesystem, database, secrets, resources, background_work, telemetry) | structured view of `capability-manifest.yaml`; 404 unknown name |
| `/bundles/{name}/ledger?limit=N` | GET | `{bundle, verified, count, blocks:[…]}` | last N blocks of `runtime-ledger.jsonl` (default/all; `limit=0` → no blocks; negative/non-integer `limit` → 400); `verified` = the same ledger verification as `/evidence/verify` (fail-closed: no key → `verified:false`); 404 unknown name |

All responses JSON. No auth (tailnet-only). Everything read-only.

## Explicitly NOT in v2 (deferred, with reasons)

- **Push/pull of bundles over HTTP** — git is the distribution layer
  (proposal verdict, v1 usage confirmed).
- **AuthN/AuthZ, multi-user, orgs** — tailnet-only; revisit only if the
  registry is exposed beyond the tailnet or serves multiple teams.
- **`/diff?bundle=A@v1&bundle=A@v2`** — the repo currently has exactly one
  version per bundle (versions live in git history, not as parallel bundle
  dirs); a diff endpoint over git history belongs to the version-event
  milestone, not this one. Enumerated here so the gap is explicit.
- **Mutating evidence** — evidence is append-only by design.
- **`/bundles/{name}/implementation`-level views** — implementations are
  replaceable candidates; the catalog surface ends at protocol-level views.

## Acceptance checks (this doc is the contract)

1. `pdd index` and `pdd search` produce the documented catalog/search (pyyaml
   required — pinned in the Dockerfile; without it they exit with an explicit
   error, never fabricated data), exit codes as specified, and index the sealed
   `user-registry` bundle.
2. Every HTTP endpoint above exists, is read-only, returns JSON, 404s on
   unknown bundle, 400s on missing `q`, and never mutates the repo.
3. `pdd search idempotent` and `/search?q=idempotent` both surface
   user-registry B-001 (the idempotent-creation invariant).
4. `/bundles?status=sealed` returns user-registry; `depends_on` filter works
   (currently empty dependency graph → no matches).
5. The full test suite passes (`make test`), `git diff --check` is clean, and
   a post-mutation review runs before the final answer.
