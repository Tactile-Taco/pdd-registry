---
name: pdd-workflow
description: "Run the PDD loop end-to-end in this repo: lint bundle, validate candidate, build evidence chain, seal, wire CI gates."
---

# PDD Workflow (single-agent loop for pdd-registry)

## Naming — instance vs repository (do not conflate)

"M6 pdd-registry"         = the RUNNING instance: deployed service on the M6's
                            staging guest (k3s microvm), tailnet-only, HTTP/MCP
                            endpoint. https://pdd-registry.<STAGING_TAILSCALE_DNS>
"pdd-registry repository" = the GIT repo Tactile-Taco/pdd-registry: protocols,
  / "pdd-registry github"   implementations, evidence, CLI, server, CI.
  / "pdd-registry git repo"

Rule: "repository" / "github" / "git repo" / "checkout" / "worktree" → the repo.
"instance" / "service" / "deployed" / "endpoint" / "host" → ALWAYS "M6 pdd-registry".
Bare "pdd-registry" only when unambiguous (protocol bundle name, skill name).

## Role

Ties the PDD team skills into one executable loop for a single agent working in
this repository. The repo is a registry: protocol bundles are the durable
artifacts; implementations, validators, and evidence are versioned alongside
them (docker-like: bundles are portable, digestable, and sealable units).

## Registry integration
- The loop STARTS with the registry: `pdd registry index` +
  `search` (pdd-registry-client DECIDE) to find adoptable protocols before
  authoring anything. The registry is the shared state between iterations —
  `git pull origin main` + re-index before each loop pass.
- Work in DAG order: standalone bundles (no unvalidated `depends_on`) are
  linted/sealed/validated/evidenced FIRST (parallel-safe); dependents only
  after their leaves admit (DECIDE item 0). CI (validator-loop,
  staging-deploy) re-runs the gates on main automatically.

## The loop

```
author -> lint -> seal -> generate -> validate -> evidence -> (CI) -> runtime attestation -> remediate
```

1. **Author** (`pdd-protocol-author`): draft the bundle under `pdd-bundles/<name>/` with status `draft`. Record every assumption in `ambiguity-log.md`.
2. **Lint**: `make lint` (or `pdd workflow lint pdd-bundles/<name>`) — required files, unique invariant ids, `must` invariants mapped to validators, resolvable handshakes.
3. **Seal** (`pdd-contract-negotiator`): zero open conflicts, versions pinned, `status: sealed`, negotiation minutes committed. Sealed bundles change only via version events. Sealing precedes implementation: generators work against `sealed`-only bundles (step 4).
4. **Generate** (`pdd-implementation-generator`): implement against `status: sealed` bundles; emit a `candidate-manifest.json`. No self-declared compliance.
5. **Validate** (`pdd-validation-engine`): `make validate` — structural (schema), behavioral (property tests + mutation sanity), operational (capability/sandbox checks). Verdict + `validation-results.json`.
6. **Evidence** (`pdd-evidence-keeper`): `make evidence` — build signed evidence object `E = H(P, I, V, R, t)`, genesis ledger block, verify chain.
7. **CI** (`pdd-ci-architect`): workflows under `ci-templates/`; `make ci-install` for a scoped credential. Locally: `make all` runs lint -> validate -> evidence.
8. **Runtime** (`pdd-runtime-verifier`): harness/drill mode until a deployment exists; degradation records for `should`-severity performance invariants.
9. **Remediate** (`pdd-remediation-orchestrator`): violation -> repair context -> regenerate -> full re-validation -> `remediation-outcome` block. Never hot-patch.

## Rules for this repository

- Bundles live in `pdd-bundles/`; one directory per protocol, versioned inside `protocol.yaml`.
- Implementations live in `implementations/<protocol>/<variant>/`; each is a candidate until the Validation Engine admits it.
- Evidence lives in `evidence/<protocol>/`; the runtime ledger is append-only.
- Performance invariants are `should` by default (project policy: provisional infrastructure). Capability invariants are `must`.
- Anything that cannot be validated in harness mode is recorded as `skip` with a reason — never as `pass` without enforcement.
- `make all` must pass before any commit that touches a bundle or implementation.
