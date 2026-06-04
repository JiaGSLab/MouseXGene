#!/usr/bin/env bash
# Sync local project to production host (run on your Mac).
# Usage: ./scripts/rsync_to_server.sh
# Optional: SERVER=ubuntu@your.host ./scripts/rsync_to_server.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${SERVER:-ubuntu@YOUR_SERVER}"
REMOTE_DIR="${REMOTE_DIR:-~/apps/MouseXGene}"

if [[ "${SERVER}" == "ubuntu@YOUR_SERVER" ]]; then
  echo "ERROR: Set SERVER before running, e.g. export SERVER=ubuntu@your.host" >&2
  exit 1
fi

echo "Syncing ${ROOT} -> ${SERVER}:${REMOTE_DIR}"
rsync -avz --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude '.env.prod' \
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
