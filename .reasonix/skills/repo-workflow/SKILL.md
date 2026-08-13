---
name: repo-workflow
description: Manage pdd-registry development — branch model (feature to PR to dev to main), staging deploy via CI to k3s, commit and evidence discipline.
---

# pdd-registry Development Workflow

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

## Carry-forward — protocol naming decision (2026-08-13)

The pdd-repository → pdd-registry rename (repo, deployed instance, GHCR image)
was a skills-only decision: the sealed pdd-registry v1.0.0 protocol bundle was
NOT amended (zero old-name strings in bundles/evidence/implementations; evidence
binds to content digests, not names). The NEXT SUBSTANTIVE protocol update MUST
record this repo/instance/protocol naming decision in
pdd-bundles/pdd-registry/ambiguity-log.md (Resolved, v1.1.0+) and re-ground
.reasonix/skills/pdd-protocol-author/references/ambiguity-taxonomy.md:90 (it cites
order-handler, absent from this repo since the annotation-app extraction). Full
loop: seal → three-layer validate → evidence build → commit/push (CI
validator-loop) → publish via PDD_PUBLISH_TOKEN (Infisical misc-secrets, prod
env; 401 without it).

## Branch model

- **main** — release branch. Sealed, evidence-verified. Changes arrive only via
  merge from `dev` (or hotfix PRs through the same gates). Never commit directly.
- **dev** — integration branch. Every push to `dev` triggers the staging deploy
  workflow. This is where verified work lands.
- **feature/\<name\>** — working branches, one per change, opened as a **git
  worktree** (`using-git-worktrees`). Merged to `dev` via PR.

## Flow

```
feature/* → PR → pdd-pr-gates (lint + test + validate + compatibility)
                → merge to dev → pdd-staging-deploy (build → ghcr → k3s apply)
                → promote to main (sealed bundle, evidence verified, release gate)
```

## Staging deploy contract (k3s, M6 microvm)

1. `nix build .#image` — nix2container image from the flake.
2. Push to `ghcr.io/tactile-taco/<project>` (auth via `GHCR_PAT` from Infisical).
3. `deploy/k8s.yaml` — Deployment + Service + Ingress (`ingressClassName: traefik`),
   image pinned by digest where possible (evidence integrity: what staging runs
   must be what was attested).
4. Apply via `kubectl` on the staging guest (self-hosted runner on M6 preferred;
   never expose staging to the public internet).
5. Verify: rollout status, ingress responds, health endpoint OK.

Secrets (`PDD_EVIDENCE_KEY`, registry creds) come from Infisical as k8s Secrets —
never in manifests, commits, or chat. See `m6-agent-workstation` for infra access.

## Commit & evidence discipline

- Run the PDD loop before any commit touching a bundle or implementation:
  `make lint && make test && make validate && make evidence`.
- Work is committed only when verified; unverified work stays in the worktree.
- Evidence is append-only; tamper or superseded attestations are incidents, not
  silent corrections.
- Use the superpowers planning skills for non-trivial changes: `brainstorming` →
  `writing-plans` → `executing-plans`, and `requesting-code-review` before merging.
- Performance/resource invariants stay `should`-tier (provisional infra).

## CI workflows (ci-templates/)

- `pdd-pr-gates.yml` — PR gate: lint, compatibility.
- `pdd-validator-loop.yml` — push to main: full loop + evidence verify.
- `pdd-staging-deploy.yml` — push to dev: build, push, deploy to staging k3s.
- `pdd-nightly.yml` — extended property runs + evidence verify + issue on failure.
- `pdd-release-gate.yml` — tag push: all sealed, loop green, evidence attached.

Install with `make ci-install` (needs a credential with `workflow` scope).

## Checkpoints — stop and ask before

- Changing `nixos-infra` / M6 infra beyond the documented runbook.
- Creating or rotating Infisical secrets or k8s Secrets.
- Pushing to `main` directly (use PRs).
- Editing shared skills or AGENTS.md — propose first.
