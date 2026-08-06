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
# Machine addresses are NEVER hardcoded in this public repo — STAGING_HOST and
# STAGING_DNS come from the GitHub Actions secrets (which map to Infisical's
# STAGING_TAILSCALE_IP / STAGING_TAILSCALE_DNS in misc-secrets; see the
# m6-agent-workstation skill). The evidence key is passed as env and stored
# only in the cluster Secret.
set -euo pipefail

: "${GITHUB_TOKEN:?Set GITHUB_TOKEN to a PAT with write:packages}"
: "${STAGING_HOST:?Set STAGING_HOST (Infisical misc-secrets)}"
: "${STAGING_DNS:?Set STAGING_DNS (Infisical misc-secrets)}"
: "${PDD_EVIDENCE_KEY:?Set PDD_EVIDENCE_KEY (must match the key the evidence was signed with)}"

PROJECT="pdd-repository"
IMAGE="ghcr.io/tactile-taco/${PROJECT}:latest"

echo "==> Building image (docker)"
docker build -t "${IMAGE}" .

echo "==> Pushing ${IMAGE}"
printf '%s' "${GITHUB_TOKEN}" | docker login ghcr.io -u tactile-taco --password-stdin
docker push "${IMAGE}"

# Pin the manifest to the digest we just pushed (k8s.yaml must never drift to a
# stale :latest). RepoDigests[0] is "host/name@sha256:…".
DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${IMAGE}" | sed 's/.*@//')"
echo "==> Deploying image digest ${DIGEST}"

echo "==> Creating evidence Secret on ${STAGING_HOST} (idempotent)"
# The key is piped via stdin (--from-env-file=/dev/stdin) so it never appears
# in any process listing or shell history.
printf 'PDD_EVIDENCE_KEY=%s\n' "${PDD_EVIDENCE_KEY}" \
  | ssh "${STAGING_HOST}" 'sudo k3s kubectl create secret generic pdd-evidence-key \
      --from-env-file=/dev/stdin --dry-run=client -o yaml | sudo k3s kubectl apply -f -'

echo "==> Applying manifest (host + image digest substituted) to ${STAGING_HOST}"
MANIFEST="deploy/k8s.yaml"
sed -e "s/__STAGING_HOST__/${PROJECT}.${STAGING_DNS}/" \
    -e "s|image: ghcr.io/tactile-taco/${PROJECT}.*|image: ghcr.io/tactile-taco/${PROJECT}@sha256:${DIGEST}|" \
    "${MANIFEST}" \
  | ssh "${STAGING_HOST}" 'sudo k3s kubectl apply -f -'
echo "==> Bouncing the deployment to pull the new image"
ssh "${STAGING_HOST}" "sudo k3s kubectl rollout restart deployment/${PROJECT} && \
  sudo k3s kubectl rollout status deployment/${PROJECT} --timeout=120s"

echo "==> Live at https://${PROJECT}.${STAGING_DNS}"
