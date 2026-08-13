# pdd Service — Client-Facing Feature Enumeration (draft, pre-build)

Purpose: define what the minimal pdd service exposes to developers, so the
full registry server's feature set follows from real usage rather than
assumption. Version 1 of this doc is written BEFORE the service is built
(milestone 2 of the deployment goal); it is the contract the service is
checked against.

## The service in one sentence

A network-reachable face of the pdd-registry toolchain: it serves the
registry's evidence state over HTTP and exposes the pdd CLI for interactive
use, so developers and agents can verify what the registry attests without
cloning the repo.

## Actors

1. **Developer (human)** — checks whether a bundle is sealed, whether an
   implementation was admitted, and whether the evidence chain verifies.
2. **Agent (LLM/CI)** — programmatic access to the same answers for gates and
   automation.
3. **Operator** — deploys/pushes new evidence; uses the CLI inside the pod.

## Client-facing surface (v1)

### HTTP (the service)

| Endpoint | Method | Returns | Notes |
|---|---|---|---|
| `/healthz` | GET | `pdd-service: ok` | liveness; no auth |
| `/evidence/verify` | GET | JSON: `{ok, blocks, files_checked}` | runs the ledger + evidence-object verification over the in-container evidence/ |
| `/evidence/admission` | GET | JSON list of admitted artifact digests + verdicts | read-only view of admission evidence |
| `/bundles` | GET | JSON list of `{name, version, status}` | read-only registry index from pdd-bundles/ |

All responses are JSON (except /healthz). No auth in v1 (tailnet-only access);
auth becomes a v2 concern with the registry server.

### Exec (in-pod CLI)

- `kubectl exec deploy/pdd-registry -- python3 scripts/pdd.py bundle lint`
- `... -- python3 scripts/pdd.py evidence verify <name>`
- `... -- make test` (full candidate suite runs in-container, env-scrubbed)

The container is the repo: bundles, implementations, validators, evidence, and
skills are all present at `/opt/pdd`.

## Explicitly NOT in v1 (candidates for the registry server)

- Push/pull of bundles over HTTP (registry server).
- AuthN/AuthZ, multi-user, orgs.
- Search API over invariants/capabilities.
- Mutating evidence (evidence is append-only; new evidence arrives via deploy).

## Questions this doc must answer before the registry server

1. Do developers want `/bundles` to be searchable (by invariant/capability)?
2. Is `/evidence/verify` enough, or do they need per-bundle attestation detail?
3. Should the registry server speak OCI (push/pull) or is git still the
   distribution layer with HTTP as a *read* API?
4. When does auth become necessary (public vs tailnet-only)?

Answers come from using v1 — the registry server spec will be written against
what v1 shows developers actually reaching for.
