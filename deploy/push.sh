#!/usr/bin/env bash
# Build the image (docker), push to ghcr.io, and deploy to the staging k3s
# cluster. Verified path (see docs/retrospective-deploy.md): the nix
# `skopeo copy "nix:…"` transport is broken on current nixpkgs, so we use the
# docker build + push path; the nix2container flake pipeline remains for the
# self-hosted-runner future.
#
# Usage (from a machine with docker on the tailnet + infisical):
#   eval "$(infisical export --projectId <misc-secrets-id> --env prod --format=dotenv-eval --silent)"
#   GITHUB_TOKEN=<pat with write:packages> PDD_EVIDENCE_KEY=<key> ./deploy/push.sh
#
# Machine addresses are NEVER hardcoded in this public repo — they come from
# the environment, which the GitHub Actions workflow fills from the
# STAGING_TAILSCALE_IP / STAGING_TAILSCALE_DNS / STAGING_SSH_KEY secrets
# (canonical names, matching Infisical misc-secrets; see the
# m6-agent-workstation skill). The evidence key is passed as env and stored
# only in the cluster Secret.
set -euo pipefail

: "${GITHUB_TOKEN:?Set GITHUB_TOKEN to a PAT with write:packages}"
: "${STAGING_TAILSCALE_IP:?Set STAGING_TAILSCALE_IP (Infisical misc-secrets)}"
: "${STAGING_TAILSCALE_DNS:?Set STAGING_TAILSCALE_DNS (Infisical misc-secrets)}"
: "${PDD_EVIDENCE_KEY:?Set PDD_EVIDENCE_KEY (must match the key the evidence was signed with)}"

PROJECT="pdd-repository"
IMAGE="ghcr.io/tactile-taco/${PROJECT}:latest"
STAGING_USER="${STAGING_SSH_USER:-tacticaltaco}"
STAGING_TARGET="${STAGING_USER}@${STAGING_TAILSCALE_IP}"

# ssh to the staging guest. CI path (STAGING_SSH_KEY set, self-hosted runner):
# the runner has no ssh agent keys, so push.sh needs the key material written
# to a file first (the workflow does this in $RUNNER_TEMP) and the guest host
# key is pinned in deploy/staging-known_hosts (fail-closed: a re-created guest
# with new keys fails until that file is rotated). Manual path keeps the
# caller's default ssh behaviour.
ssh_guest() {
  if [ -n "${STAGING_SSH_KEY:-}" ]; then
    ssh -i "${STAGING_SSH_KEY}" -o BatchMode=yes -o ConnectTimeout=10 \
      -o StrictHostKeyChecking=yes \
      -o UserKnownHostsFile="${STAGING_KNOWN_HOSTS:-deploy/staging-known_hosts}" \
      "${STAGING_TARGET}" "$@"
  else
    ssh "${STAGING_TARGET}" "$@"
  fi
}

echo "==> Building image (docker)"
docker build -t "${IMAGE}" .

echo "==> Pushing ${IMAGE}"
# WARP-mediated egress is intermittently flaky for the docker daemon
# (ENETUNREACH to ghcr.io, observed on the M6 runner); the push is idempotent
# (same digest), so retry a few times before giving up.
push_ok=0
for attempt in 1 2 3; do
  # login can fail with the same egress flake; it is not fatal by itself
  printf '%s' "${GITHUB_TOKEN}" | docker login ghcr.io -u tactile-taco --password-stdin || true
  if docker push "${IMAGE}"; then
    push_ok=1
    break
  fi
  echo "==> docker push attempt ${attempt} failed (intermittent egress); retrying in 10s" >&2
  sleep 10
done
[ "${push_ok}" = 1 ] || { echo "docker push failed after 3 attempts" >&2; exit 1; }
# Don't leave the PAT in the runner's docker config (HOME is under /run, but
# be explicit).
docker logout ghcr.io >/dev/null 2>&1 || true

# Pin the manifest to the digest we just pushed (k8s.yaml must never drift to a
# stale :latest). RepoDigests[0] is "host/name@sha256:…"; strip the scheme so
# the substitution below can write the full "…@sha256:<hex>" reference.
DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${IMAGE}" | sed 's/.*@sha256://')"
echo "==> Deploying image digest ${DIGEST}"

echo "==> Creating evidence Secret on ${STAGING_TAILSCALE_IP} (idempotent)"
# The key is piped via stdin (--from-env-file=/dev/stdin) so it never appears
# in any process listing or shell history.
printf 'PDD_EVIDENCE_KEY=%s\n' "${PDD_EVIDENCE_KEY}" \
  | ssh_guest 'sudo k3s kubectl create secret generic pdd-evidence-key \
      --from-env-file=/dev/stdin --dry-run=client -o yaml | sudo k3s kubectl apply -f -'

echo "==> Creating pdd-postgres Secret on ${STAGING_TAILSCALE_IP} (idempotent, self-healing)"
# v1.2: the registry runs DB-backed. The password is generated ONCE and lives
# only in the cluster Secret (tailnet-only staging); the secret is re-applied
# on every deploy with the CURRENT password so the URL (host pdd-postgres =
# the Service name) self-heals without rotating the password (a rotation
# would need a postgres restart + re-seed). Generation is runner-side with
# coreutils (the guest's ssh shell has no openssl); content flows via stdin.
PG_PW="$(ssh_guest 'sudo k3s kubectl get secret pdd-postgres -o jsonpath={.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d)"
if [ -z "${PG_PW}" ]; then
  PG_PW="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
fi
printf 'POSTGRES_PASSWORD=%s\nPDD_DATABASE_URL=postgresql://pdd:%s@pdd-postgres:5432/pdd\n' \
  "${PG_PW}" "${PG_PW}" \
  | ssh_guest 'sudo k3s kubectl create secret generic pdd-postgres \
      --from-env-file=/dev/stdin --dry-run=client -o yaml | sudo k3s kubectl apply -f -'

echo "==> Applying postgres manifest (v1.2 DB-backed registry, S-006)"
ssh_guest 'sudo k3s kubectl apply -f -' < deploy/postgres.yaml

echo "==> Waiting for postgres to be ready"
ssh_guest 'sudo k3s kubectl rollout status deployment/pdd-postgres --timeout=180s'

echo "==> Applying manifest (host + image digest substituted) to ${STAGING_TAILSCALE_IP}"
MANIFEST="deploy/k8s.yaml"
# Escape & for sed (the values are operator-controlled, but never trust them).
DNS_ESC="${STAGING_TAILSCALE_DNS//&/\\&}"
SUBSTITUTED="$(sed -e "s/__STAGING_HOST__/${PROJECT}.${DNS_ESC}/" \
    -e "s|image: ghcr.io/tactile-taco/${PROJECT}.*|image: ghcr.io/tactile-taco/${PROJECT}@sha256:${DIGEST}|" \
    "${MANIFEST}")"
# Fail closed if the digest substitution did not land (e.g. manifest drift):
# re-applying a stale pinned digest would silently roll staging back.
printf '%s' "${SUBSTITUTED}" | grep -q "image: ghcr.io/tactile-taco/${PROJECT}@sha256:${DIGEST}" \
  || { echo "manifest substitution failed for ${MANIFEST}" >&2; exit 1; }
# Same guard for the host rule: __STAGING_HOST__ must not survive the sed.
printf '%s' "${SUBSTITUTED}" | grep -q "host: ${PROJECT}." \
  || { echo "host substitution failed for ${MANIFEST}" >&2; exit 1; }
printf '%s' "${SUBSTITUTED}" \
  | ssh_guest 'sudo k3s kubectl apply -f -'
echo "==> Bouncing the deployment to pull the new image"
ssh_guest "sudo k3s kubectl rollout restart deployment/${PROJECT} && \
  sudo k3s kubectl rollout status deployment/${PROJECT} --timeout=120s"

echo "==> Seeding the DB-backed registry (git -> DB on deploy, brownfield sync)"
# v1.2: the registry catalog lives in PostgreSQL. Each sealed bundle is
# published with the evidence attested by its LATEST ledger block (the
# author-side chain stays the source of truth; the DB is the serving layer).
# publish is idempotent (B-006) so re-deploys are no-ops. The seed loop runs
# INSIDE the registry pod: the image ships the repo + evidence at /opt/pdd,
# and publish targets pdd+http://localhost:8080 — the runner shell can
# neither resolve tailnet DNS nor trust the staging cert, so in-cluster
# publishing avoids both.
for BUNDLE_DIR in pdd-bundles/*/; do
  BNAME="$(basename "${BUNDLE_DIR}")"
  echo "==> seeding ${BNAME}"
  ssh_guest "sudo k3s kubectl exec deploy/${PROJECT} -- sh -c '
    EV=\$(python3 /opt/pdd/scripts/pdd.py evidence latest ${BNAME}) &&
    python3 /opt/pdd/scripts/pdd.py publish /opt/pdd/pdd-bundles/${BNAME} \
      --evidence \"\$EV\" --registry pdd+http://localhost:8080
  '" || { echo "seed failed for ${BNAME}" >&2; exit 1; }
done

echo "==> Live at https://${PROJECT}.${STAGING_TAILSCALE_DNS}"
