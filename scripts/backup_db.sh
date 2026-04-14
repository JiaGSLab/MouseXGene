#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${1:-./backups}"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
BACKUP_FILE="${OUTPUT_DIR}/mousexgene_${TIMESTAMP}.sql"

mkdir -p "${OUTPUT_DIR}"

echo "Creating backup: ${BACKUP_FILE}"
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U "${POSTGRES_USER:-mousexgene}" "${POSTGRES_DB:-mousexgene}" > "${BACKUP_FILE}"
echo "Backup completed: ${BACKUP_FILE}"
