#!/usr/bin/env bash
# Push the current HEAD to justrunmy.app's git-based deploy endpoint.
#
# justrunmy.app builds from the `deploy` branch on push (Dokku-style).
# The remote URL contains basic-auth credentials, so keep them out of the
# repo: put them in ~/.justrunmy.env (or export before invoking).
#
# Required env:
#   JRM_GIT_URL   full URL incl. user:token, e.g.
#                 https://USER:TOKEN@justrunmy.app/git/r_XXXXXXX
#
# Usage:
#   ./scripts/deploy_git.sh          # push current HEAD to remote's `deploy`
#   ./scripts/deploy_git.sh feature  # push local `feature` branch
set -euo pipefail

if [[ -f "${HOME}/.justrunmy.env" ]]; then
  # shellcheck source=/dev/null
  source "${HOME}/.justrunmy.env"
fi

: "${JRM_GIT_URL:?Set JRM_GIT_URL (see scripts/deploy_git.sh header)}"

LOCAL_REF="${1:-HEAD}"

echo ">> Pushing ${LOCAL_REF} -> justrunmy.app:deploy"
git push -u "${JRM_GIT_URL}" "${LOCAL_REF}:deploy"
echo ">> Done. Watch the build in the justrunmy.app dashboard."
