# Retrospective — Deploy run (M1 push → M3 verified live)

Date: 2026-08-06 · Goal: minimal pdd service live on k3s staging, PDD methodology

## Outcome (verified)

- Image built and pushed: ghcr.io/tactile-taco/pdd-repository:latest
  (digest sha256:b3397643…; built on the M6 via Dockerfile path).
- Deployed: Deployment + Service + Ingress (traefik) on the staging guest
  (k3s v1.32.7), pod Running, rollout complete.
- Verified end-to-end through the ingress:
  * /healthz → ok
  * /bundles → user-registry 1.0.0 sealed
  * /evidence/verify → ok:true, blocks:1, "digest+signature valid, ledger-attested"
  * /evidence/admission → admitted artifact listed, verified:true
  * kubectl exec: `pdd bundle lint` → PASS (13 invariants)

## What worked

- The repo-workflow/m6-agent-workstation contract held: flake + manifests +
  push.sh + AGENTS.md from M1 were usable as-is; only the image-transport
  needed a workaround.
- Fail-closed design paid off: the cluster Secret supplies the same key that
  signed the committed evidence (dev-local-key), so /evidence/verify is a real
  verification, not a stub.
- Secrets discipline: GHCR_PAT and addresses never appeared in argv,
  manifests, or chat (stdin pipes + Infisical at use time).

## What needed human judgment / environment workarounds

1. **skopeo `nix:` transport is broken on current nixpkgs** — both
   `skopeo-nix2container` and `nix2container` were dropped from nixpkgs, and
   the nlewo flake ships no CLI. The nixos-infra `#project` template's
   push.sh (`skopeo copy "nix:…"`) needs updating (proposal below).
2. **M6 ghcr.io egress is intermittently unreachable per-IP** (documented
   QEMU/NAT issue; curl to one IP works, docker login to another fails).
   Worked around by pushing from the laptop's docker daemon (which reaches
   registries), via docker save → scp → load → push.
3. **Sandbox constraints**: nix store read-only (build on M6); docker build
   containers lack DNS (build on M6); HOME read-only breaks `docker login`
   (use `DOCKER_CONFIG=$(mktemp -d)`); tailnet MagicDNS doesn't resolve here
   (verify via staging IP + Host header).
4. **The flake `.#image` build succeeded on the M6** but was never pushed as a
   nix image — the Dockerfile path (same pinned versions) shipped instead.
   Keep both; the nix path is the future runner's job.

## Agent-workflow analysis

- LLM did well: deriving the flake, catching the address-leak on self-review,
  stdlib-only service, fail-closed honesty, systematic transport debugging.
- Human judgment needed: choosing to build on the M6 vs the laptop, accepting
  the Dockerfile fallback over more nix archaeology, and approving credential
  use (Infisical/gh/docker push). A solo LLM with no gates would likely have
  burned hours on the nix transport before pivoting.

## Proposals (do not apply unilaterally)

1. **nixos-infra template fix**: replace `skopeo copy "nix:…"` in the
   `#project` template's push.sh with either (a) docker save/load + push, or
   (b) pin an old nixpkgs with skopeo-nix2container — verify which the M6 can
   sustain.
2. ~~**Evidence key**~~ **DONE (2026-08-06)**: staging previously used
   `dev-local-key`. Rotated to the production `PDD_EVIDENCE_KEY` from Infisical
   (nixos-infra, prod): evidence re-signed under it (commit 3adcf4d), k8s
   Secret recreated from it (stdin, never argv), image `c92ae4d0` deployed —
   live `/evidence/admission` reports `verified: true` and `/evidence/verify`
   `ok: true`. Evidence is now single-key (rotation = re-sign + redeploy; see
   AGENTS.md invariants).
3. **Runner**: the manual path works; the self-hosted runner
   (docs/runner-proposal.md + ci-templates/pdd-staging-deploy.yml) is the
   follow-up for push-to-dev automation — deferred, not abandoned.
4. **AGENTS.md**: add the Dockerfile build path + DOCKER_CONFIG note + the
   "verify via IP+Host header when MagicDNS is unavailable" tip.
5. **Skill**: consider a `pdd-staging-deploy` practice skill capturing the
   verified deploy recipe (build on M6 → save/load/push → jump-apply →
   IP+Host verify) once it stabilizes.
