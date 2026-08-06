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
  printf '%s' "${GITHUB_TOKEN}" | docker login ghcr.io -u tactile-taco --password-stdin
  if docker push "${IMAGE}"; then
    push_ok=1
    break
  fi
  echo "==> docker push attempt ${attempt} failed (intermittent egress); retrying in 10s" >&2
  sleep 10
done
[ "${push_ok}" = 1 ] || { echo "docker push failed after 3 attempts" >&2; exit 1; }

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
printf '%s' "${SUBSTITUTED}" \
  | ssh_guest 'sudo k3s kubectl apply -f -'
echo "==> Bouncing the deployment to pull the new image"
ssh_guest "sudo k3s kubectl rollout restart deployment/${PROJECT} && \
  sudo k3s kubectl rollout status deployment/${PROJECT} --timeout=120s"

echo "==> Live at https://${PROJECT}.${STAGING_TAILSCALE_DNS}"
