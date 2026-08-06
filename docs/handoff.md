# pdd-repository — Handoff

**Date:** 2026-08-06 · **Branch:** `pdd-work` @ `a9d9bc9` (== `origin/pdd-work`) · **Worktree:** `/home/TacticalTaco/.reasonix/global-workspace/pdd-repository-wt` · **Main checkout:** `/home/TacticalTaco/.reasonix/global-workspace/pdd-repository` (on `main`, older)

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
- **Staging deploy live** at `https://pdd-repository.staging.tail4904d2.ts.net`
  (k3s guest on the M6): `/healthz` ok, `/evidence/admission` → `verified: true`,
  `/evidence/verify` → `ok: true` (1 block). Image pinned in `deploy/k8s.yaml`
  at `ghcr.io/tactile-taco/pdd-repository@sha256:c92ae4d0…` (manifest-list digest
  from `docker push`; amd64 manifest `fe4a1d83` = M6 build).
- **GitHub secrets** (Tactile-Taco/pdd-repository): `GHCR_PAT`,
  `PDD_EVIDENCE_KEY`, `STAGING_HOST`, `STAGING_DNS`.
- **Tests:** 13 pass (`make test` requires the real key; see gotchas).
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

1. rsync worktree → M6: `rsync -az -e "ssh -F /dev/null -o ConnectTimeout=6 -o BatchMode=yes" <wt>/ tacticaltaco@100.114.69.6:/home/tacticaltaco/pdd-repository/`
2. Build on M6 (has docker): `docker build -t pdd-repository:latest . && docker save | gzip -1 > /tmp/pdd-image.tgz`
3. `scp` to laptop, `docker load`, `docker login ghcr.io -u tactile-taco --password-stdin` (PAT via stdin), `docker push ghcr.io/tactile-taco/pdd-repository:latest`
4. Update the digest pin in `deploy/k8s.yaml` to the pushed digest.
5. Apply via jump: `ssh -F /dev/null -J tacticaltaco@100.114.69.6 -p 2222 tacticaltaco@localhost 'sudo -n k3s kubectl apply -f -'` (Secret first: `create secret generic pdd-evidence-key --from-env-file=/dev/stdin`), then `rollout restart` + `rollout status`.

## 5. Secrets / infra

- **Infisical** (CLI authed on laptop): nixos-infra project = `GHCR_PAT`,
  `PDD_EVIDENCE_KEY`, `SOPS_AGE_KEY`; misc-secrets project
  (`5598630f-4109-47d9-bbfb-91bac16ac92c`) = `M6_*`, `LAPTOP_*`,
  `STAGING_TAILSCALE_DNS`, `STAGING_TAILSCALE_IP`.
- **Naming mismatch (next-iteration item):** GitHub secrets are
  `STAGING_HOST`/`STAGING_DNS`, but Infisical names them
  `STAGING_TAILSCALE_IP`/`STAGING_TAILSCALE_DNS`. Align before enabling CI.
- **M6:** `ssh -F /dev/null tacticaltaco@100.114.69.6` (tailscale IP, avoid
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
  `*.tail4904d2.ts.net` — verify with
  `curl -sk --resolve pdd-repository.staging.tail4904d2.ts.net:443:<IP> …`
- **Evidence is single-key (HMAC-SHA256):** rotation = re-sign + redeploy.
  Re-sign on the M6 with `--sandbox` (docker), rsync evidence back, commit.
- **Never put PDD_EVIDENCE_KEY in argv/echo** — pipe via stdin
  (`printf '%s' "$KEY" | … --password-stdin` / `--from-env-file=/dev/stdin`).
- **Reviews:** every mutation round needs a fresh `review` after it; keep the
  review the last action before the final answer.

## 7. Next steps (not done, flagged)

1. Self-hosted runner on the M6 (nixos-infra `services.github-runners` module)
   + install `ci-templates/` via `make ci-install`; fix the STAGING_* naming.
2. Fold `pdd-work` → `main` (PR or fast-forward) so main carries the re-signed
   chain.
3. Registry server iteration (per the user's roadmap): `pdd search`/index,
   full registry server design (proposal docs exist in the session history).
4. Optional nits: try/except around the `infisical` subprocess in
   `src/tests/test_server.py:35`; `mktemp -d` HOME cleanup in the Makefile;
   stale generic template in `.reasonix/skills/pdd-ci-architect/assets/`.
