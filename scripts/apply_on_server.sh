#!/usr/bin/env bash
# Apply synced code on the production server (run ON the server after rsync).
# Usage: cd ~/apps/MouseXGene && ./scripts/apply_on_server.sh

set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
if [[ -f .env.prod ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.prod
  set +a
fi
POSTGRES_USER="${POSTGRES_USER:-mousexgene}"
POSTGRES_DB="${POSTGRES_DB:-mousexgene}"
IDENT_RE='^[A-Za-z_][A-Za-z0-9_]*$'
if [[ ! "${POSTGRES_USER}" =~ ${IDENT_RE} || ! "${POSTGRES_DB}" =~ ${IDENT_RE} ]]; then
  echo "ERROR: POSTGRES_USER and POSTGRES_DB must be simple PostgreSQL identifiers." >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}" media staticfiles

STAMP="$(date +%Y%m%d_%H%M%S)"
echo "Database backup -> ${BACKUP_DIR}/mousexgene_${STAMP}.sql"
${COMPOSE} exec -T db pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  > "${BACKUP_DIR}/mousexgene_${STAMP}.sql"

echo "Building the new web image before migrations..."
${COMPOSE} build web

echo "Ensuring bind-mounted writable directories belong to the non-root app user..."
${COMPOSE} run --rm --no-deps --user root web \
  chown -R 1000:1000 /app/staticfiles /app/media

echo "Running migrations (required after code sync; missing migrations cause HTTP 500)..."
if ! ${COMPOSE} run --rm web python manage.py migrate --noinput; then
  echo "ERROR: migrate failed. Fix errors above before using the site." >&2
  exit 1
fi
echo "Pending migrations check:"
${COMPOSE} run --rm web python manage.py showmigrations breeding colony | grep -E '^\s+\[ \]' || true

echo "Collecting static files (clear stale manifest entries)..."
${COMPOSE} run --rm web python manage.py collectstatic --noinput --clear

echo "Recreating web + nginx containers (pick up code, dependencies, and config)..."
${COMPOSE} up -d --force-recreate web nginx

echo "Waiting for gunicorn..."
sleep 4
${COMPOSE} exec web python manage.py check

echo "Running post-restart health check..."
if ! curl -kfsS --retry 5 --retry-delay 2 https://127.0.0.1/health/ -H 'Host: jialabmouse.top' >/dev/null; then
  echo "ERROR: production health check failed after restart." >&2
  exit 1
fi

BUILD="$(grep -E '^APP_RELEASE=' .env.prod 2>/dev/null | cut -d= -f2- || echo settings-default)"
echo "Done. Build tag: ${BUILD}"
echo "Verify cages page source contains: list_filters.js and table-sort-link"
echo "Hard refresh: https://jialabmouse.top/cages/ (Cmd+Shift+R)"
