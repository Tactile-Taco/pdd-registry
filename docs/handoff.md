# pdd-repository — Handoff

**Date:** 2026-08-06 · **Branch:** `pdd-work` — pushed and folded into `main` at `53daf0f` (all commits on origin; `main` == `origin/main` == `origin/pdd-work` == `53daf0f`) · **Worktree:** `/home/TacticalTaco/.reasonix/global-workspace/pdd-repository-wt` · **Main checkout:** `/home/TacticalTaco/.reasonix/global-workspace/pdd-repository` (on `main`, synced)

> ⚠️ This repo is public: real addresses never appear here. Machine addresses
> (M6 tailscale IP, staging tailscale IP/DNS, ports) live in Infisical
> misc-secrets (`M6_TAILSCALE_IP`, `STAGING_TAILSCALE_IP`, `STAGING_TAILSCALE_DNS`).

---

## 1. What this is

A PDD (Protocol-Driven Development) repository: sealed protocol bundles as the
primary artifacts (docker-image-like), candidate implementations, a three-layer
Validator Loop, an HMAC-signed Evidence Chain + ledger, and a small HTTP service
(`src/server.py`) that serves and verifies the evidence. PDD skills live in
`.reasonix/skills/` (10 skills, generalized from the kimi set, bugs fixed).

## 2. Current state (all verified)

- **Evidence re-signed** under the production `PDD_EVIDENCE_KEY` (Infisical,
  nixos-infra project `7a2f10fc-2d47-4008-a817-3f5493dc7476`, env `prod`).
  Admission digest `3136cfa4…` == ledger `evidence_digest` (byte-coupling proven
  by sha256sum). Artifact digest `5614cd8f…`.
- **Staging deploy live** at `https://pdd-repository.<STAGING_TAILSCALE_DNS>`
  (k3s guest on the M6): `/healthz` ok, `/evidence/admission` → `verified: true`,
  `/evidence/verify` → `ok: true` (1 block). Image pinned in `deploy/k8s.yaml`
  at `ghcr.io/tactile-taco/pdd-repository@sha256:c92ae4d0…` (manifest-list digest
  from `docker push`; amd64 manifest `fe4a1d83` = M6 build).
- **GitHub secrets** (Tactile-Taco/pdd-repository): `GHCR_PAT`,
  `PDD_EVIDENCE_KEY`, `STAGING_HOST`, `STAGING_DNS` (plus the default
  `GITHUB_TOKEN` used by the `gh` steps in nightly/release-gate).
- **Tests:** 36 pass (`make test` with the real key: 10 candidate + 26 service,
  incl. the v2 surface; +5 hardening tests from the two review rounds).
  `src/tests/test_registry.py` (23 tests) runs WITHOUT the evidence key.
- **Self-hosted runner DONE:** `m6-pdd` (repo-scoped, label `staging-deploy`)
  live on the M6 via nixos-infra `modules/github-runner.nix` (nixpkgs-unstable
  runner v2.335.1; 25.05's v2.330.0 is deprecated by GitHub). CI workflows
  installed via `make ci-install` and pushed (workflow scope granted by
  refresh). `pdd-validator-loop` passed on main (1m23s). `pdd-staging-deploy`
  E2E was blocked by a GitHub Actions outage (see §8); re-dispatch after it
  clears.
- **Secrets renamed** to match Infisical: `STAGING_TAILSCALE_IP` /
  `STAGING_TAILSCALE_DNS` (old `STAGING_HOST`/`STAGING_DNS` deleted), plus
  new `STAGING_SSH_KEY` (runner→guest ed25519, pubkey authorized on the
  guest; host-key verification disabled for the tailnet-only guest).
- **CI templates** in `ci-templates/` (NOT installed to `.github/workflows/` —
  needs workflow-scope credentials; run `make ci-install`).

## 3. Key commands

```bash
export PDD_EVIDENCE_KEY=$(infisical secrets get PDD_EVIDENCE_KEY --projectId 7a2f10fc-2d47-4008-a817-3f5493dc7476 --env prod --plain --silent)
make lint        # bundle linter
make test        # candidate suites (env -i scrubbed) + service surface (needs the key)
make validate    # three-layer Validator Loop
make evidence    # build + verify signed evidence (needs the key)
make all         # commit gate (lint+test+validate+evidence; needs the key)
```

## 4. Deploy pipeline (the path that actually works)

1. rsync worktree → M6: `rsync -az -e "ssh -F /dev/null -o ConnectTimeout=6 -o BatchMode=yes" <wt>/ tacticaltaco@<M6_TAILSCALE_IP>:/home/tacticaltaco/pdd-repository/`
2. Build on M6 (has docker): `docker build -t pdd-repository:latest . && docker save | gzip -1 > /tmp/pdd-image.tgz`
3. `scp` to laptop, `docker load`, `docker login ghcr.io -u tactile-taco --password-stdin` (PAT via stdin), `docker push ghcr.io/tactile-taco/pdd-repository:latest`
4. Update the digest pin in `deploy/k8s.yaml` to the pushed digest.
5. Apply via jump: `ssh -F /dev/null -J tacticaltaco@<M6_TAILSCALE_IP> -p 2222 tacticaltaco@localhost 'sudo -n k3s kubectl apply -f -'` (Secret first: `create secret generic pdd-evidence-key --from-env-file=/dev/stdin`), then `rollout restart` + `rollout status`.

## 5. Secrets / infra

- **Infisical** (CLI authed on laptop): nixos-infra project = `GHCR_PAT`,
  `PDD_EVIDENCE_KEY`, `SOPS_AGE_KEY`; misc-secrets project
  (`5598630f-4109-47d9-bbfb-91bac16ac92c`) = `M6_*`, `LAPTOP_*`,
  `STAGING_TAILSCALE_DNS`, `STAGING_TAILSCALE_IP`.
- **Naming mismatch (next-iteration item):** GitHub secrets are
  `STAGING_HOST`/`STAGING_DNS`, but Infisical names them
  `STAGING_TAILSCALE_IP`/`STAGING_TAILSCALE_DNS`. Align before enabling CI.
- **M6:** `ssh -F /dev/null tacticaltaco@<M6_TAILSCALE_IP>` (from Infisical misc-secrets;
  printing it). Guest (staging k3s) at `localhost:2222` from the M6; stale host
  key fix = `ssh-keygen -R "[localhost]:2222"` after guest re-creates. M6 shell
  is zsh with no python3 on non-login ssh; use `nix develop -c bash -c '…'`
  inside the repo, or `nix run nixpkgs#…`.

## 6. Gotchas

- **`nix` skopeo `nix:` transport is broken** on current nixpkgs
  (`skopeo-nix2container` / `nix2container` CLI absent) — use the
  docker save/scp/load/push path above. `push.sh` already uses docker.
- **`make validate`/`make all` on a docker-less machine** regenerates
  `evidence/user-registry/validation/*.results.json` with O-001/O-002 →
  `skip`, diverging from the attested sandbox-pass version. Restore with
  `git checkout -- evidence/…` (the committed version is the attested one).
- **Tailnet DNS doesn't resolve from laptop/M6 shells** for
  `*.<STAGING_TAILSCALE_DNS>` — verify with
  `curl -sk --resolve pdd-repository.<STAGING_TAILSCALE_DNS>:443:<STAGING_TAILSCALE_IP> …`
- **Evidence is single-key (HMAC-SHA256):** rotation = re-sign + redeploy.
  Re-sign on the M6 with `--sandbox` (docker), rsync evidence back, commit.
- **Never put PDD_EVIDENCE_KEY in argv/echo** — pipe via stdin
  (`printf '%s' "$KEY" | … --password-stdin` / `--from-env-file=/dev/stdin`).
- **Reviews:** every mutation round needs a fresh `review` after it; keep the
  review the last action before the final answer.

## 7. Next steps (not done, flagged)

1. ~~Self-hosted runner + CI install~~ **DONE (this session)**: `m6-pdd` on
   the M6 (nixos-infra 58e57d7), `make ci-install` committed on main
   (53daf0f), STAGING secrets aligned. Remaining: re-run `pdd-staging-deploy`
   once the GitHub Actions outage clears (see §8) — the runner itself is
   online and job-ready.
2. ~~Fold `pdd-work` → `main`~~ **DONE (this session)**: fast-forwarded
   `dc928e8..492bd01` via `git push origin pdd-work:main` — main carries the
   re-signed evidence chain; primary checkout synced to `origin/main`.
3. ~~Registry server iteration~~ **DONE (commit 56f1aa6 on pdd-work)**: feature
   enumeration doc first (`docs/service-features-v2.md`), shared search index
   (`src/registry_index.py`), `pdd index`/`pdd search`, and the v2 read API
   (`/search`, filterable `/bundles`, `/bundles/{name}` +
   invariants/capabilities/ledger). Retrospective:
   `docs/retrospective-registry-v2.md`. `/diff` deferred to a version-event
   milestone; push/pull + auth still explicitly out of scope. **Re-verified
   this session**: `make test` 36 pass (10 candidate + 26 service),
   `git diff --check` clean, CLI `pdd index`/`pdd search idempotent` run
   live. All commits pushed to origin and folded into `main`.
4. Optional nits: try/except around the `infisical` subprocess in
   `src/tests/test_server.py:35`; `mktemp -d` HOME cleanup in the Makefile;
   stale generic template in `.reasonix/skills/pdd-ci-architect/assets/`.

## 8. CI + runner status (2026-08-06)

- Workflows installed: `pdd-pr-gates`, `pdd-validator-loop` (passed on
  main), `pdd-nightly`, `pdd-release-gate`, `pdd-staging-deploy` (trigger:
  push to `main` + `workflow_dispatch`; was `dev`, which never existed).
- **YAML gotcha fixed**: inline flow maps with `${{ … }}`
  (`env: { PDD_EVIDENCE_KEY: … }`) are invalid — GitHub rejects the whole
  workflow file. All env/with blocks are now block-form.
- Runner on the M6: `github-runner-pdd.service` (user `github-runner`,
  group `docker`). Registration token is a 1h sops secret
  (`github_runner_pdd_token` in nixos-infra secrets.yaml): do NOT change
  url/name/labels/token without minting a fresh token
  (`gh api -X POST repos/Tactile-Taco/pdd-repository/actions/runners/registration-token`)
  and rebuilding — or replace it with a fine-grained PAT (`Administration`
  read on the repo) for durability.
- **GitHub Actions outage at 15:22Z 2026-08-06** (status: major, partial
  outage, "workflow runs failing to start"): blocked the first
  `pdd-staging-deploy` E2E at "Getting action download info". Not our
  config: first job of the same commit downloaded actions fine before the
  incident. Re-dispatch after it resolves.
