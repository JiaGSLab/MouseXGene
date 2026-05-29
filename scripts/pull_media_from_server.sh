#!/usr/bin/env bash
# Sync uploaded files (strain line PDFs, etc.) from production to local ./media
# Usage: ./scripts/pull_media_from_server.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${SERVER:-ubuntu@118.195.218.49}"
REMOTE_DIR="${REMOTE_DIR:-~/apps/MouseXGene}"

mkdir -p "${ROOT}/media"

echo "Syncing media ${SERVER}:${REMOTE_DIR}/media/ -> ${ROOT}/media/"
rsync -avz "${SERVER}:${REMOTE_DIR}/media/" "${ROOT}/media/"

echo "Done."
