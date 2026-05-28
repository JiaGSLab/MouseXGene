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

echo "Running migrations (required after code sync; missing migrations cause HTTP 500)..."
if ! ${COMPOSE} exec web python manage.py migrate --noinput; then
  echo "ERROR: migrate failed. Fix errors above before using the site." >&2
  exit 1
fi
echo "Pending migrations check:"
${COMPOSE} exec web python manage.py showmigrations breeding colony | grep -E '^\s+\[ \]' || true

echo "Collecting static files (clear stale manifest entries)..."
${COMPOSE} exec web python manage.py collectstatic --noinput --clear

echo "Recreating web + nginx containers (pick up code, static, nginx config)..."
${COMPOSE} up -d --force-recreate web nginx

echo "Waiting for gunicorn..."
sleep 4
${COMPOSE} exec web python manage.py check

BUILD="$(grep -E '^APP_RELEASE=' .env.prod 2>/dev/null | cut -d= -f2- || echo settings-default)"
echo "Done. Build tag: ${BUILD}"
echo "Verify cages page source contains: list_filters.js and table-sort-link"
echo "Hard refresh: https://jialabmouse.top/cages/ (Cmd+Shift+R)"
