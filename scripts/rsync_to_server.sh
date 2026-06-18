#!/usr/bin/env bash
# Sync local project to production host (run on your Mac).
# Usage: ./scripts/rsync_to_server.sh
# Optional: SERVER=ubuntu@your.host ./scripts/rsync_to_server.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/load_deploy_env.sh"

if [[ "${SERVER}" == "ubuntu@YOUR_SERVER" ]]; then
  echo "ERROR: Copy .env.deploy.example to .env.deploy and set SERVER (local file, not on GitHub)." >&2
  exit 1
fi

echo "Syncing ${ROOT} -> ${SERVER}:${REMOTE_DIR}"
rsync -avz --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.claude' \
  --exclude '.ruff_cache' \
  --exclude '.env' \
  --exclude '.env.deploy' \
  --exclude '.env.prod' \
  --exclude 'backups/' \
  --exclude 'media' \
  --exclude 'staticfiles' \
  --exclude 'postgres_data' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "${ROOT}/" "${SERVER}:${REMOTE_DIR}/"

echo ""
echo "Done. Now SSH to the server and run:"
echo "  cd ~/apps/MouseXGene && ./scripts/apply_on_server.sh"
