# Self-hosted GitHub Actions runner on the M6 for staging deploys

Status: **implemented 2026-08-06** (runner `m6-pdd` on the M6,
`nixos-infra` `modules/github-runner.nix`, label `staging-deploy`).

## Why

- pdd-registry is public; staging is tailnet-only. GitHub-hosted runners
  cannot reach it without importing a tailnet credential into CI.
- A repo-scoped, label-restricted self-hosted runner on the M6 deploys
  in-network, reuses docker + sops secrets already there.
- The M6 is NixOS-declared, so the runner is a flake change with rollback.

## nixos-infra change (implemented)

```nix
# modules/github-runner.nix, imported by hosts/agent-workstation/configuration.nix
sops.secrets.github_runner_pdd_token = { owner = "root"; };
users.users.github-runner = { isSystemUser = true; group = "github-runner";
                              extraGroups = [ "docker" ]; };
services.github-runners.pdd = {
  enable = true;
  url = "https://github.com/Tactile-Taco/pdd-registry";
  name = "m6-pdd";
  tokenFile = config.sops.secrets.github_runner_pdd_token.path;
  extraLabels = [ "staging-deploy" ];
  user = "github-runner";
  group = "docker";
  extraPackages = [ pkgs.docker ];
  # 25.05's github-runner (v2.330.0) is deprecated by GitHub; use the
  # nixpkgs-unstable package (v2.335.1). Unstable dropped node20, so
  # nodeRuntimes = [ "node24" ].
  package = inputs.nixpkgs-unstable.legacyPackages.${pkgs.system}.github-runner;
  nodeRuntimes = [ "node24" ];
};
```

- Registration token: minted via the GitHub API
  (`POST /repos/Tactile-Taco/pdd-registry/actions/runners/registration-token`,
  valid 1h) and stored sops-encrypted in `nixos-infra` `secrets/secrets.yaml`.
  Note: re-registration (config/token change) needs a fresh token; a durable
  fine-grained PAT (`Administration: read` on the repo) can replace it later.
- The runner user needs `docker` (push.sh builds/pushes via docker) and ssh
  access to the staging guest. The GitHub secret `STAGING_SSH_KEY` holds the
  private key MATERIAL (ed25519, no passphrase); the workflow materializes it
  to `$RUNNER_TEMP/ci-staging-key` (0600) before calling push.sh. The guest's
  host key is pinned in `deploy/staging-known_hosts` (fail-closed: rotate that
  file whenever the guest microvm is re-created; a stale entry blocks the
  deploy on purpose).

## Secrets in GitHub (repo Actions secrets, names aligned with Infisical)

| Secret | Source |
|---|---|
| `GHCR_PAT` | PAT with `write:packages` (Infisical `nixos-infra`) |
| `STAGING_TAILSCALE_IP` | Infisical misc-secrets (renamed from `STAGING_HOST`) |
| `STAGING_TAILSCALE_DNS` | Infisical misc-secrets (renamed from `STAGING_DNS`) |
| `STAGING_SSH_KEY` | dedicated ed25519 key (public half authorized on the guest) |
| `PDD_EVIDENCE_KEY` | Infisical `nixos-infra` (pre-existing) |

## Trigger

`pdd-staging-deploy.yml` runs on push to `main` (+ `workflow_dispatch`).
Before the runner existed it had no trigger branch and no runner: deploys ran
from the laptop via `deploy/push.sh` (manual, tailnet + Infisical env).
