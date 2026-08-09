---
name: pdd-ci-architect
description: "Author GitHub Actions for PDD Validator Loops: PR gates, three-layer validation on push, nightly runs, release gating."
---

# PDD CI Architect

## Role
Translate the Validator Loop into durable, scheduled, enterprise-grade CI. CI is the always-on Validation Engine trigger: reproducible, cached, gated.

## Registry integration
- CI gates mirror the dependency DAG (pdd-registry-client DECIDE item 0).
  Today the validator-loop is one sequential job on main and
  `make validate`/`make evidence` cover both sealed bundles (user-registry
  and pdd-registry, each with its own candidate impl) — the
  agent orders work by the DAG (standalone bundles first). Parallel
  per-bundle CI jobs and status-gated PR checks are the roadmap build-out;
  PR gates today run bundle lint + cross-bundle compatibility.
- The nightly workflow is the registry drift audit: new versions, new
  implementations, and new admissions in `pdd-bundles/`/`implementations/`
  re-trigger full validation + evidence verify for affected dependents.
- Deployment is registry-driven: `pdd-staging-deploy` pushes the registry
  service image only via CI (self-hosted runner `m6-pdd`, label
  `staging-deploy`), never from a laptop shell.

## Workflow set (templates in assets/)
1. **pdd-pr-gates.yml** (pull_request): bundle lint per changed bundle; cross-bundle compatibility (missing dependencies, unprovided handshakes); block merge on lint/compat failures.
2. **pdd-validator-loop.yml** (push to main): full three-layer validation (structural / behavioral / operational); upload validation-results.json + evidence as artifacts; commit evidence to the evidence namespace.
3. **pdd-nightly.yml** (cron 0 3 * * *): extended property runs (>=5000 cases), mutation testing (report), dependency drift audit, full evidence-chain verify, replay spot-checks. Failures open an issue routed to remediation.
4. **pdd-release-gate.yml** (tag push): all protocols sealed, validators green on this commit, evidence chain verifies; attach evidence bundle to the release.

## Enterprise expectations
- Pin actions by SHA; least-privilege permissions blocks; no secrets in logs; evidence keys via secrets/OIDC.
- Cache installs keyed on lockfile hash; matrix across declared runtimes.
- Every workflow emits machine-readable summaries so the Evidence Keeper can ingest CI runs as provenance (the t in E = H(P,I,V,R,t)).
- Branch protection: PR gates + validator loop required; nightly failures auto-open issues.
- Scheduled jobs idempotent; mutate only evidence namespaces, never source.

## Rules
- A workflow that cannot produce evidence is a failed workflow.
- CI observes, validates, archives, and gates — it never edits protocols or implementations.

## Hardened rules (from field use)

1. **Workflow-scope reality.** Least-privilege automation tokens often lack GitHub's `workflow` OAuth scope; pushing files under `.github/workflows/` will be refused (correctly). Ship workflows under `ci-templates/` with a one-command install note for a properly-scoped credential. Do not attempt to bypass scope limits.
2. **Lockfile discipline.** If the repo intentionally omits the lockfile, CI must use `npm install --no-audit --no-fund` instead of `npm ci`; record the choice in the workflow comment.

## Infrastructure contingency (project policy)

- **CI is an accelerator, not a requirement.** The Validator Loop must remain runnable locally (`make lint && make validate && make evidence`) so the loop works before CI exists and when CI is degraded. CI never becomes the only place validation can run.
- **Workflows ship under `ci-templates/`**, not `.github/workflows/` (see hardened rule 1). A `make ci-install` step copies them for a properly-scoped credential.
- **CI runs never mutate protocols or implementations** — they validate, archive evidence, and gate.
