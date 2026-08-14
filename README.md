# pdd-registry

> **Code is transient; protocol is sovereign.**

A working **Protocol-Driven Development** registry: versioned, sealable protocol
bundles as the durable artifacts — with candidate implementations, a three-layer
Validator Loop, and a verifiable Evidence Chain + Dynamic Evidence Ledger living
next to them. Think of it as *Docker for protocols*:

| Docker concept      | pdd-registry concept                                       |
|---------------------|--------------------------------------------------------------|
| Dockerfile / image  | `pdd-bundles/<name>/` — versioned, sealed, digestable bundle |
| Container           | `implementations/<name>/<variant>/` — candidate realization  |
| Registry + log      | `evidence/<name>/` — Evidence Chain + append-only ledger     |
| `docker run`        | `pdd run` — smoke-run a candidate in a sandbox               |
| Image layers/digest | bundle digest + artifact digest, bound in `E = H(P, I, V, R, t)` |

## Layout

```
pdd-bundles/            # THE protocol registry (durable, versioned, sealed)
  user-registry/        #   first bundle: idempotent user creation (paper case study)
  pdd-registry/         #   dogfood bundle: catalog search + read views (registry core)
implementations/        # candidate realizations (replaceable; never authoritative)
  user-registry/python-stdlib/
evidence/               # admission evidence objects, discovery logs, runtime ledger
ci-templates/           # GitHub Actions workflows (install via `make ci-install`)
.reasonix/skills/       # the PDD team skills that drive this workflow

The CLI (linter, three-layer validation engine, evidence chain, registry
client) lives in the **pdd-cli** repo (`Tactile-Taco/pdd-cli`, installed as the
`pdd` binary) — single source of truth for the loop tooling; the Makefile,
CI, and server call the installed `pdd`. This repo owns the data: bundles,
implementations, evidence.
```

## Quickstart

```bash
export PDD_EVIDENCE_KEY=$(infisical secrets get PDD_EVIDENCE_KEY --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain --silent)  # the key the committed evidence is signed with
make lint       # hardened bundle linter over pdd-bundles/*
make test       # candidate suites (scrubbed) + service verification surface (uses $PDD_EVIDENCE_KEY)
make validate   # three-layer Validator Loop -> verdict + validation-results.json
make evidence   # signed evidence object + genesis ledger block + ledger verify (needs $PDD_EVIDENCE_KEY)
make all        # the whole loop (the commit gate; needs $PDD_EVIDENCE_KEY exported)
```

The CLI (install from the pdd-cli repo):

```bash
pdd workflow lint                          # lint every bundle
pdd workflow validate user-registry --impl implementations/user-registry/python-stdlib --sandbox --pbt-runs 5000
pdd workflow evidence build user-registry --impl implementations/user-registry/python-stdlib --validation-resource <url>
pdd workflow evidence verify user-registry
pdd workflow run user-registry --impl implementations/user-registry/python-stdlib --sandbox
pdd registry search idempotent             # HTTP client for the deployed instance
```

The same index powers the service's v2 read API (docs/service-features-v2.md):
`/search?q=`, `/bundles?status=&depends_on=`, `/bundles/{name}`,
`/bundles/{name}/invariants`, `/capabilities`, `/ledger` — all read-only,
served by `src/server.py`.

## How a protocol gets admitted

1. **Author** the bundle (`.reasonix/skills/pdd-protocol-author`): typed
   handshakes, S/B/O invariants, capability manifest, ambiguity log.
2. **Lint** — must pass (`make lint`) before any seal.
3. **Generate** a candidate (`pdd-implementation-generator`): tests carry
   invariant lineage (`B-001`, `S-002`, …); the output is a candidate manifest,
   never a verdict.
4. **Validate** — three layers, jointly necessary:
   - **S**: schema conformance, contract tests
   - **B**: property tests + **mutation sanity** (a deliberate mutant must FAIL)
   - **O**: import/dependency allowlist, forbidden-call scan, optional docker
     sandbox (`--network none --read-only`), advisory benchmark
5. **Evidence** — `E = H(P, I, V, R, t)` signed evidence object + genesis ledger
   block; tampering is detectable (`pdd evidence verify`).
6. **Seal** — status flips to `sealed`; changes require a version event and
   renewed negotiation.
7. **Operate** — runtime attestation appends to the same ledger; violations
   become repair contexts, never hot patches.

## Project policy (infrastructure contingencies)

- **Performance invariants are `should`-tier by default** (see O-005): budgets
  are measured and recorded as observations, not admission gates. Hard
  capability invariants (network, filesystem, secrets, dependencies, background
  work) stay `must` — they are enforceable with sandboxing alone.
- **Harness-first**: the whole loop runs locally with zero infrastructure
  (`make all`). CI (GitHub Actions, see `ci-templates/`) is an accelerator, not
  a requirement; workflows ship as templates because a least-privilege token
  often cannot push `.github/workflows/` (use `make ci-install` with a scoped
  credential).
- **Never fake enforcement**: a check that could not actually run records
  `skip` with a reason — never `pass`.
- **Skills are part of the repo**: `.reasonix/skills/` is the canonical copy of
  the PDD team skills (generalized from the original kimi/Codex set, bugs
  fixed, policy added).

## Adding a protocol

```bash
mkdir pdd-bundles/my-protocol
# copy the template set:
cp -r .reasonix/skills/pdd-protocol-author/assets/templates/* pdd-bundles/my-protocol/
# author, then:
make lint && pdd workflow seal pdd-bundles/my-protocol
```

## Evidence chain integrity

The evidence scripts **fail closed**: signing and verification require the
`PDD_EVIDENCE_KEY` environment variable (export the same key at sign and verify
time). Use the Infisical value (see Quickstart); CI reads the `PDD_EVIDENCE_KEY`
repository secret. Without the key, evidence operations refuse to run — the
HMAC default is deliberately not a secret, so no silent "verified" claim is
possible under the public default.

```bash
python3 .reasonix/skills/pdd-evidence-keeper/scripts/evidence_chain.py verify evidence/user-registry/runtime-ledger.jsonl
```

The ledger is append-only; corrections are new blocks. A `verify` failure is a
governance incident: quarantine, notify remediation (`pdd-remediation-orchestrator`).

## Reference

Protocol-Driven Development: Governing Generated Software Through Invariants
and Continuous Evidence — He & Yu, OpenKedge (arXiv:2605.12981).
