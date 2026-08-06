# Proposal: self-hosted GitHub Actions runner on the M6 for staging deploys

Status: **proposal** — requires a change to `nixos-infra` (different repo) and
provisioning a registration token. Not applied from this sandbox.

## Why

- pdd-repository is public; staging is tailnet-only. GitHub-hosted runners
  cannot reach it without importing a tailnet credential into CI.
- A repo-scoped, label-restricted self-hosted runner on the M6 deploys
  in-network, reuses nix + skopeo + sops/Infisical secrets already there.
- The M6 is NixOS-declared, so the runner is a flake change with rollback.

## nixos-infra change (draft, no secrets literal)

```nix
# host config (agent-workstation)
services.github-runners.pdd-staging = {
  enable = true;
  url = "https://github.com/Tactile-Taco/pdd-repository";
  tokenFile = config.age.secrets.github-runner-token.path;  # or sops
  extraPackages = [ pkgs.nix pkgs.skopeo ];
  labels = [ "self-hosted" "staging-deploy" ];
  runnerGroup = "default";
  # Replace existing services? No: co-exists with any other runners.
};
```

- Registration token: create a fine-grained PAT (repo scope `pdd-repository`,
  read-only metadata is enough to register a repo-scoped runner) or use the
  runner-registration token from a script; store in sops/Infisical
  (`nixos-infra` project), never in the public repo.
- The runner user needs `nix` (writable store) + `skopeo` + network to
  ghcr.io (via the M6) — covered by `extraPackages`.

## Secrets needed in GitHub (repo Actions secrets)

| Secret | Source |
|---|---|
| `GHCR_PAT` | PAT with `write:packages` (Infisical) |
| `STAGING_HOST` | Infisical misc-secrets |
| `STAGING_DNS` | Infisical misc-secrets |

## Fallback (documented in the goal)

Until the runner exists, deploys run from the laptop via `deploy/push.sh`
(manual, tailnet + Infisical env). The workflow `pdd-staging-deploy.yml`
waits for the runner.
