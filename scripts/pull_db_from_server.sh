#!/usr/bin/env bash
# Download a PostgreSQL dump from production into ./backups (run on your Mac).
# Usage: ./scripts/pull_db_from_server.sh [output_dir]
#
# Then restore locally: ./scripts/restore_db_local.sh backups/mousexgene_prod_YYYYMMDD_HHMMSS.sql

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/load_deploy_env.sh"
OUTPUT_DIR="${1:-${ROOT}/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${OUTPUT_DIR}/mousexgene_prod_${TIMESTAMP}.sql"

if [[ "${SERVER}" == "ubuntu@YOUR_SERVER" ]]; then
  echo "ERROR: Copy .env.deploy.example to .env.deploy and set SERVER (local file, not on GitHub)." >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

echo "Dumping production DB on ${SERVER}..."
ssh "${SERVER}" bash -s -- "${REMOTE_DIR}" > "${BACKUP_FILE}" <<'EOF'
set -euo pipefail
REMOTE_DIR="$1"
cd "${REMOTE_DIR}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
USER="$(grep -E '^POSTGRES_USER=' .env.prod 2>/dev/null | cut -d= -f2- || echo mousexgene)"
DB="$(grep -E '^POSTGRES_DB=' .env.prod 2>/dev/null | cut -d= -f2- || echo mousexgene)"
${COMPOSE} exec -T db pg_dump -U "${USER}" "${DB}"
EOF

BYTES="$(wc -c < "${BACKUP_FILE}" | tr -d ' ')"
if [[ "${BYTES}" -lt 1000 ]]; then
  echo "ERROR: dump looks too small (${BYTES} bytes). Check SSH and server DB." >&2
  exit 1
fi

echo "Saved: ${BACKUP_FILE} (${BYTES} bytes)"
echo ""
echo "Restore into local dev:"
echo "  ./scripts/restore_db_local.sh ${BACKUP_FILE}"
