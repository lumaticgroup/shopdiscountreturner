#!/usr/bin/env bash
# Build and push the bot's Docker image to justrunmy.app's registry.
#
# justrunmy.app hands you a per-app registry host and a set of creds. Keep
# them out of the repo: put them in ~/.justrunmy.env (or export inline).
#
# Required env:
#   JRM_REGISTRY   e.g. jdr-n36so95qz.justrunmy.app
#   JRM_IMAGE      e.g. n36so95qz                    (repo path on the registry)
#   JRM_USER       registry username
#   JRM_PASSWORD   registry password / token
#
# Optional:
#   JRM_TAG        image tag (default: v1)
#
# Usage:
#   ./scripts/deploy_docker.sh           # builds + pushes ${JRM_TAG:-v1}
#   ./scripts/deploy_docker.sh v2        # override tag
set -euo pipefail

if [[ -f "${HOME}/.justrunmy.env" ]]; then
  # shellcheck source=/dev/null
  source "${HOME}/.justrunmy.env"
fi

: "${JRM_REGISTRY:?Set JRM_REGISTRY (e.g. jdr-XXXXXXXX.justrunmy.app)}"
: "${JRM_IMAGE:?Set JRM_IMAGE (registry repo path)}"
: "${JRM_USER:?Set JRM_USER}"
: "${JRM_PASSWORD:?Set JRM_PASSWORD}"

TAG="${1:-${JRM_TAG:-v1}}"
FULL="${JRM_REGISTRY}/${JRM_IMAGE}:${TAG}"

echo ">> Logging in to ${JRM_REGISTRY}"
echo "${JRM_PASSWORD}" | docker login -u "${JRM_USER}" --password-stdin "${JRM_REGISTRY}"

echo ">> Building ${FULL}"
docker build -t "${FULL}" .

echo ">> Pushing ${FULL}"
docker push "${FULL}"

echo ">> Done. Point the justrunmy.app service at ${FULL}."
