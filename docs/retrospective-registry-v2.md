# Retrospective — Registry Server v2 (read API + search)

Date: 2026-08-06 · Branch: pdd-work · Goal: evolve the minimal service toward
the full registry server (docs/registry-server-proposal.md), feature
enumeration first per PDD methodology.

## What was planned (docs/service-features-v2.md, written BEFORE build)

- CLI: `pdd index` (catalog) + `pdd search <q>` (ranked search over names,
  purpose, invariant statements, capability keys) — one shared index.
- HTTP (additive over v1): `/search?q=`, filterable
  `/bundles?status=&depends_on=`, `/bundles/{name}` summary,
  `/invariants?severity=`, `/capabilities`, `/ledger?limit=N`.
- Explicitly deferred: push/pull, auth, `/diff` (single-version bundles),
  evidence mutation.

## What was built

- `src/registry_index.py` — stdlib catalog + inverted search over
  `pdd-bundles/*`: S/B/O invariant views, capabilities view, ledger view,
  field-weighted AND-token ranking. **Shared verbatim** by CLI and HTTP so the
  two surfaces can never disagree on the index.
- `scripts/pdd.py` — `pdd index` / `pdd search` (lazy import keeps every other
  command stdlib-only; `sys.path` insert for the shared module).
- `src/server.py` — `do_GET` rewritten to urlparse routing; six v2 routes
  added; `/bundles` gained filters while staying backward compatible;
  `_admission` deduped onto a shared `_ledger_valid()`; fail-closed `verified`
  flag on the ledger route.
- `src/tests/test_registry.py` — 17 tests, **no PDD_EVIDENCE_KEY required**
  (ledger route's `verified` is asserted to be a real bool, fail-closed).

## Verification

- `python3 -m pytest src/tests -q` → 20 passed (17 new + 3 pre-existing v1
  admission tests — no v1 regression).
- `python3 -m pytest implementations/ -q` (scrubbed) → 10 passed.
- `git diff --check` → clean.
- Live server smoke test: `/healthz` ok, `/search?q=idempotent` → purpose
  (score 5) + B-001 (score 3), `/bundles?status=sealed` filtered,
  `/invariants?severity=should` → only O-005, `/ledger?limit=1` → 1 block,
  unknown bundle → 404, missing `q` → 400.

## What worked

- **Feature enumeration before build** caught scope early: writing the doc
  forced the `/diff` deferral (the repo has one version per bundle — a diff
  endpoint over git history is a version-event milestone, not this one).
- **One shared index** paid off immediately: `pdd search idempotent` and
  `/search?q=idempotent` return identical ranked results by construction.
- **Fail-closed discipline held**: ledger route reports `verified` from the
  real HMAC chain verification (False without the key), never an assumption;
  v2 views raise explicit errors rather than fabricating catalog data when
  pyyaml is absent.

## What needed judgment (bugs found & fixed during build)

- protocol.yaml puts `purpose`/`depends_on`/`provides` at the **top level,
  siblings of `protocol:`** — the index initially read them from the
  `protocol` subdict (empty `provides`, purpose never searchable). Caught by
  the live `pdd search` output, fixed to read the top-level keys.
- Refactor regression: extracting `_ledger_valid()` from `_admission`
  orphaned `verify_script`, breaking the v1 admission tests — caught
  immediately by the pre-existing suite (proving its value as a regression
  net).
- Test-harness issues, not server bugs: urllib raises `HTTPError` for 4xx
  (helper now decodes it), `/healthz` is text not JSON (decode helper), and
  AND-token search needed a realistic co-occurring pair ("user registry") —
  "idempotent network" never co-occurs in one entry by design.

**Post-review fixes**: `?limit=0` previously returned ALL blocks (Python's
`blocks[-0:]`); now 0 → no blocks and negative/non-integer limits → 400.
`load_bundle` now tolerates null invariant lists (`or []`) and a null
`protocol:` key instead of crashing the whole catalog. The feature doc now
states the pyyaml requirement explicitly (fail-closed without it) and the
per-entry AND semantics. One deliberate behavior change worth noting: v1
`/bundles` listed unparseable bundles via the naive parse; v2 `/bundles` (and
all v2 views) drop them and report per-bundle errors via `pdd index` — the
fail-closed direction.

**Fresh post-mutation review round** (after handoff re-verification): 3 more
findings fixed with 3 regression tests (`test_registry.py` now 21 tests):
`load_bundle` normalizes a string `depends_on` to a list (the
`/bundles?depends_on=` filter must stay exact-membership) and non-dict
`capabilities`/`provides` to `{}` (a list previously crashed `pdd index` and
`/search` with `AttributeError`); `/bundles/{name}` on a broken bundle now
returns a clear 500 with the catalog error instead of a `KeyError`; and
`ledger_view` rejects escaping bundle names (`..`, `a/b`) — defense in depth,
unreachable via HTTP since routes constrain names to real bundle dirs.
`make test` now 34 pass (10 candidate + 24 service).

**Second (should-fix) review round**: server now serves with
`ThreadingHTTPServer` (subprocess routes must not block `/healthz`); the
blanket exception handler logs the traceback and returns a generic 500
instead of echoing `str(exc)` (paths / YAML parser internals); the
broken-bundle route also returns a generic 500 with the catalog error logged;
`load_catalog` returns an empty catalog when the bundles dir is missing
(restores v1 `/bundles` behavior instead of 500-ing every route). Tests added:
positive + exact-membership `depends_on` filter, missing-dir catalog,
`server.PDD` override in the test harness. `make test` now 36 pass (10
candidate + 26 service; `test_registry.py` 23 tests).

## Registry-server implications (feeding the next iteration)

- v2 confirms the proposal's read-API direction: every endpoint stayed
  read-only, and the search index answers the "who guarantees X" questions
  developers asked in v1.
- **Next gaps when they appear**: `/diff` needs a version-event milestone
  (multi-version bundle layout or git-history access); auth stays off until
  exposure widens; the self-hosted runner (docs/runner-proposal.md) would let
  the registry server ride the staging deploy pipeline — still the
  highest-leverage infra step, unchanged by this iteration.
