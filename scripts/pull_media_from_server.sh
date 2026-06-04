#!/usr/bin/env bash
# Sync uploaded files (strain line PDFs, etc.) from production to local ./media
# Usage: ./scripts/pull_media_from_server.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${SERVER:-ubuntu@YOUR_SERVER}"
REMOTE_DIR="${REMOTE_DIR:-~/apps/MouseXGene}"

if [[ "${SERVER}" == "ubuntu@YOUR_SERVER" ]]; then
  echo "ERROR: Set SERVER before running, e.g. export SERVER=ubuntu@your.host" >&2
  exit 1
fi

mkdir -p "${ROOT}/media"

echo "Syncing media ${SERVER}:${REMOTE_DIR}/media/ -> ${ROOT}/media/"
rsync -avz "${SERVER}:${REMOTE_DIR}/media/" "${ROOT}/media/"

echo "Done."
