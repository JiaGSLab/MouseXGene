#!/usr/bin/env bash
# Sync uploaded files (strain line PDFs, etc.) from production to local ./media
# Usage: ./scripts/pull_media_from_server.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/load_deploy_env.sh"

if [[ "${SERVER}" == "ubuntu@YOUR_SERVER" ]]; then
  echo "ERROR: Copy .env.deploy.example to .env.deploy and set SERVER (local file, not on GitHub)." >&2
  exit 1
fi

mkdir -p "${ROOT}/media"

REMOTE_MEDIA="$(printf '%q' "${REMOTE_DIR}/media/")"
echo "Syncing media ${SERVER}:${REMOTE_DIR}/media/ -> ${ROOT}/media/"
rsync -avz "${SERVER}:${REMOTE_MEDIA}" "${ROOT}/media/"

echo "Done."
