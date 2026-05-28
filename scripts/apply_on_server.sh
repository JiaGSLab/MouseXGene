#!/usr/bin/env bash
# Apply synced code on the production server (run ON the server after rsync).
# Usage: cd ~/apps/MouseXGene && ./scripts/apply_on_server.sh

set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"

mkdir -p "${BACKUP_DIR}" media staticfiles

STAMP="$(date +%Y%m%d_%H%M%S)"
echo "Database backup -> ${BACKUP_DIR}/mousexgene_${STAMP}.sql"
${COMPOSE} exec -T db pg_dump -U "${POSTGRES_USER:-mousexgene}" "${POSTGRES_DB:-mousexgene}" \
  > "${BACKUP_DIR}/mousexgene_${STAMP}.sql"

echo "Running migrations..."
${COMPOSE} exec web python manage.py migrate --noinput

echo "Collecting static files..."
${COMPOSE} exec web python manage.py collectstatic --noinput

echo "Recreating web container (loads mounted code from host)..."
${COMPOSE} up -d web

echo "Reloading nginx..."
${COMPOSE} exec nginx nginx -s reload || true

echo "Done. Verify: https://jialabmouse.top/breedings/ (hard refresh)."
