# Agent Instructions — pdd-repository

Protocol-Driven Development registry: sealed protocol bundles (pdd-bundles/),
candidate implementations (implementations/), three-layer Validator Loop
(validators/), pdd CLI (scripts/pdd.py), evidence chain + append-only ledger
(evidence/), PDD skills (.reasonix/skills/). Deploys to the staging k3s cluster
as an OCI container.

## Environment

- Python 3.12+ toolchain: `pytest`, `hypothesis`, `jsonschema`, `pyyaml`.
  In the dev shell: `nix develop` (or direnv via `.envrc`).
- Do not use the system toolchain when a dev shell is available.
- Definition of done: `nix flake check` passes AND `make all` passes.

## Commands

```bash
make lint       # hardened bundle linter over pdd-bundles/*
make test       # candidate test suites (pytest + hypothesis, env-scrubbed)
make validate   # three-layer Validator Loop -> verdict + validation-results.json
make evidence   # signed evidence + ledger (needs PDD_EVIDENCE_KEY exported)
make all        # the commit gate: lint + test + validate + evidence
nix build .#image       # build the OCI image (nix2container, no daemon)
nix flake check         # flake checks (server parse, bundle lint)
python3 scripts/pdd.py bundle lint|seal|validate|evidence|run   # the pdd CLI
```

## Architecture

- `pdd-bundles/<name>/` — the protocol registry (typed handshakes, S/B/O
  invariants, capability manifest, validators, ambiguity log). Sealed = frozen.
- `implementations/<name>/<variant>/` — candidate realizations; untrusted until
  the Validation Engine admits them (candidate-manifest.json, invariant-lineaged tests).
- `validators/validate_candidate.py` — S/B/O layers + mutation sanity + docker
  sandbox; emits validation-results.json into evidence/.
- `scripts/pdd.py` — the docker-like CLI (bundle lint/seal, validate,
  evidence build/verify, run).
- `evidence/<name>/` — signed admission evidence E=H(P,I,V,R,t), discovery
  logs, append-only runtime ledger.
- `.reasonix/skills/` — the PDD team skills + repo-workflow (branch/deploy/CI).
- `src/server.py` — the minimal HTTP service (/healthz, /evidence/verify).

## Conventions

- Evidence is append-only; tamper or superseded attestations are incidents.
- `PDD_EVIDENCE_KEY` comes from Infisical / the cluster Secret — never in
  files or chat. Machine addresses are never hardcoded in this public repo.
- Performance/resource invariants stay `severity: should` (provisional infra).
- Never report `pass` for something not actually enforced (`skip` with a reason).

## Deploying

```bash
eval "$(infisical export --projectId <misc-secrets-id> --env prod --format=dotenv-eval --silent)"
GITHUB_TOKEN=<pat with write:packages> ./deploy/push.sh
```

Service: `https://pdd-repository.<STAGING_DNS>` (tailnet only). CI path:
push to `dev` → pdd-staging-deploy workflow (see ci-templates/ and
.reasonix/skills/repo-workflow).

## Invariants (do not break)

- Port in `flake.nix` (PORT / ExposedPorts) must match containerPort, Service
  targetPort, and Ingress backend port in `deploy/k8s.yaml`.
- Ingress `ingressClassName` must stay `traefik`.
- `metadata.name` / `app` label = `pdd-repository` (routing identity, unique
  on the cluster).
- `imagePullPolicy: Always` is load-bearing with the `latest` tag + rollout
  restart; prefer digest-pinned image refs when evidence integrity matters.
- Ingress host rule is substituted at deploy time (`__STAGING_HOST__`); never
  commit a real address.
