#!/usr/bin/env bash
# Replace local dev PostgreSQL data with a .sql dump (run on your Mac).
# Usage: ./scripts/restore_db_local.sh backups/mousexgene_prod_YYYYMMDD_HHMMSS.sql
# Add -y to skip confirmation.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

CONFIRM=1
SQL_FILE=""
for arg in "$@"; do
  case "${arg}" in
    -y|--yes) CONFIRM=0 ;;
    -*) echo "Unknown option: ${arg}" >&2; exit 1 ;;
    *) SQL_FILE="${arg}" ;;
  esac
done

if [[ -z "${SQL_FILE}" || ! -f "${SQL_FILE}" ]]; then
  echo "Usage: ./scripts/restore_db_local.sh [-y] path/to/backup.sql" >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-mousexgene}"
POSTGRES_DB="${POSTGRES_DB:-mousexgene}"
IDENT_RE='^[A-Za-z_][A-Za-z0-9_]*$'
if [[ ! "${POSTGRES_USER}" =~ ${IDENT_RE} || ! "${POSTGRES_DB}" =~ ${IDENT_RE} ]]; then
  echo "ERROR: POSTGRES_USER and POSTGRES_DB must be simple PostgreSQL identifiers." >&2
  exit 1
fi

if [[ "${CONFIRM}" -eq 1 ]]; then
  echo "This will ERASE all data in local database \"${POSTGRES_DB}\" and load:"
  echo "  ${SQL_FILE}"
  read -r -p "Continue? [y/N] " ans
  if [[ "${ans}" != [yY] ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo "Starting local db container..."
docker compose up -d db
sleep 2

echo "Recreating database ${POSTGRES_DB}..."
docker compose exec -T db psql -U "${POSTGRES_USER}" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS ${POSTGRES_DB};
CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};
SQL

echo "Restoring dump..."
docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 < "${SQL_FILE}"

echo "Running migrations (harmless if already applied)..."
docker compose up -d web
docker compose exec -T web python manage.py migrate --noinput

echo "Done. Local DB matches dump. Start dev server: docker compose up"
echo "Login with the same usernames/passwords as on production."
