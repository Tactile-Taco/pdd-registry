# Retrospective — M1 (deployable repo) and M2 (minimal service)

Date: 2026-08-06 · Goal: minimal pdd service on k3s staging, PDD methodology

## M1 — Deployable repo

**Done**: flake.nix (`.#image`, nix2container, pythonEnv with pytest/hypothesis/
jsonschema/pyyaml, repo content assembled into /opt/pdd, flake checks, devShell),
deploy/k8s.yaml (Deployment+Service+Ingress, traefik, Secret env plumbing),
deploy/push.sh (env-sourced credentials/addresses), AGENTS.md, .envrc.

**What worked**
- The nixos-infra `#project` template gave a proven structure; adapting it
  (pythonEnv instead of single python3, repo-content layer, checks) was
  mechanical.
- Making the container *the repo* (bundles + implementations + validators +
  evidence + skills all present) fell out of the PDD framing naturally: the
  service verifies the exact committed evidence.

**What needed judgment**
- The template hardcodes the staging address in push.sh/k8s.yaml — fine in the
  *private* nixos-infra, **wrong in a public repo**. I initially committed the
  address to k8s.yaml and caught it on self-review; fixed with
  `__STAGING_HOST__` substitution + Infisical-sourced env. Rule encoded in
  AGENTS.md: never commit real addresses in this repo.

**Blocked (environment, not repo)**
- `nix build .#image` cannot run in this sandbox: `/nix/store` and
  `/nix/var/nix/db` are read-only (host-verified; no nix-daemon). Static
  validation only (flake parses as template-derived, python compiles). The
  real build is verified on the M6 runner / laptop nix at deploy time — the
  deploy pipeline builds there anyway.

## M2 — Minimal service

**Done**: docs/service-features-v1.md (feature enumeration written BEFORE
build), src/server.py: /healthz, /bundles, /evidence/verify, /evidence/admission.

**Verified (local, system python)**:
- /healthz → ok
- /bundles → user-registry 1.0.0 sealed
- /evidence/verify → `ok:true, blocks:1` + "digest+signature valid,
  ledger-attested" (real verification over the committed ledger)
- /evidence/admission → artifact digest sha256:5614cd8f… listed
- fail-closed: no PDD_EVIDENCE_KEY → verify exits 1 with explicit refusal
  (honest `ok:false`, never a fake pass)

**Agent-workflow observations (what an LLM did well / needed humans)**
- Well: deriving the flake from the template; catching the address leak via
  self-review; keeping the service stdlib-only; fail-closed honesty matching
  the repo's evidence discipline.
- Human-needed: the M6/Infisical boundary (runner provisioning, token
  creation, secrets), and any environment with a writable nix store.

## Registry-server implications (feeding M6 of the goal)

- v1 shows developers reach for: registry index (read), evidence verification
  (read), admission view (read). All read-only — **git remains the
  distribution layer; the registry server is a read API + search**, not a
  push/pull host, unless v1 usage shows otherwise. This directly answers
  question 3 of the feature doc.
