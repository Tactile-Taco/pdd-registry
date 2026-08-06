#!/usr/bin/env bash
# Build the OCI image with Nix, push to ghcr.io, and apply to the staging k3s cluster.
#
# Usage (from a machine on the tailnet with nix + skopeo + gh + infisical):
#   eval "$(infisical export --projectId <misc-secrets-id> --env prod --format=dotenv-eval --silent)"
#   GITHUB_TOKEN=<pat with write:packages> ./deploy/push.sh
#
# Machine addresses are NEVER hardcoded in this public repo — STAGING_HOST and
# STAGING_DNS come from Infisical (see m6-agent-workstation skill).
set -euo pipefail

: "${GITHUB_TOKEN:?Set GITHUB_TOKEN to a PAT with write:packages}"
: "${STAGING_HOST:?Set STAGING_HOST (Infisical misc-secrets)}"
: "${STAGING_DNS:?Set STAGING_DNS (Infisical misc-secrets)}"

PROJECT="pdd-repository"
IMAGE="ghcr.io/tactile-taco/${PROJECT}:latest"
HOST="${STAGING_HOST}"

echo "==> Building image"
IMAGE_STORE_PATH="$(nix build .#image --no-link --print-out-paths)"

echo "==> Pushing ${IMAGE}"
# nix2container images are skopeo-pushable directly from the Nix store —
# no Docker daemon required on the build machine.
skopeo login ghcr.io -u "$(gh api user --jq .login 2>/dev/null || echo tactile-taco)" \
  --password "${GITHUB_TOKEN}"
skopeo copy "nix:${IMAGE_STORE_PATH}" "docker://${IMAGE}"

echo "==> Applying to ${HOST}"
# Substitute the staging host into a temp copy of the manifest (never commit addresses).
sed "s/__STAGING_HOST__/${PROJECT}.${STAGING_DNS}/" deploy/k8s.yaml > /tmp/pdd-repository-k8s.yaml
ssh "${HOST}" "mkdir -p /home/tacticaltaco/${PROJECT}"
scp /tmp/pdd-repository-k8s.yaml "${HOST}:/home/tacticaltaco/${PROJECT}/k8s.yaml"
rm -f /tmp/pdd-repository-k8s.yaml
ssh "${HOST}" "sudo k3s kubectl apply -f /home/tacticaltaco/${PROJECT}/k8s.yaml"

# imagePullPolicy: Always + identical tag means we must bounce the pods to re-pull
ssh "${HOST}" "sudo k3s kubectl rollout restart deployment/${PROJECT} && sudo k3s kubectl rollout status deployment/${PROJECT} --timeout=120s"

echo "==> Live at https://${PROJECT}.${STAGING_DNS}"
