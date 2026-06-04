# MouseXGene

MouseXGene is a Django + PostgreSQL system for lab-scale mouse colony management.

## Stack

- Python 3.11
- Django
- PostgreSQL
- Docker / Docker Compose
- Gunicorn
- Django Admin

## Local Development

Use `docker-compose.yml` for development.

1. Copy env file:

   ```bash
   cp .env.example .env
   ```

2. Build and run:

   ```bash
   docker compose up --build
   ```

3. Run migrations:

   ```bash
   docker compose exec web python manage.py migrate
   ```

4. Create superuser:

   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

5. Access:
   - App: `http://localhost:8000/`
   - Admin: `http://localhost:8000/admin/`

## Production Deployment (Linux Server)

Use `docker-compose.prod.yml` and Gunicorn.

1. Prepare env:

   ```bash
   cp .env.prod.example .env.prod
   ```

2. Build and start:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
   ```

3. Run migrations:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py migrate
   ```

4. Collect static files:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py collectstatic --noinput
   ```

5. Create superuser:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py createsuperuser
   ```

## Gunicorn Runtime

Production service runs:

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

(`docker-compose.prod.yml` already uses Gunicorn with worker settings.)

**HTTPS (Let’s Encrypt + Certbot on the host):** see **`deploy/README.md`** — nginx listens on 80/443, mounts `/etc/letsencrypt` and `./acme-challenge`; first-time issuance uses a temporary HTTP-only bootstrap config.

## Database Backup (PostgreSQL)

Use helper script:

```bash
POSTGRES_USER=mousexgene POSTGRES_DB=mousexgene ./scripts/backup_db.sh
```

Optional output directory:

```bash
POSTGRES_USER=mousexgene POSTGRES_DB=mousexgene ./scripts/backup_db.sh ./backups
```

This runs `pg_dump` from the DB container and writes a timestamped `.sql` file.

## Sync production data to local dev

On your Mac (SSH to the server must work). Set your server once per shell:

```bash
export SERVER=ubuntu@your.host
```

```bash
# 1) Download a fresh SQL dump from the server
./scripts/pull_db_from_server.sh

# 2) Replace your local dev database (prompts for confirmation)
./scripts/restore_db_local.sh backups/mousexgene_prod_YYYYMMDD_HHMMSS.sql

# 3) Optional: strain-line PDFs and other uploads
./scripts/pull_media_from_server.sh

docker compose up
```

`-y` skips the restore confirmation. Local `.env` should keep the same `POSTGRES_DB` / `POSTGRES_USER` names as production (password may differ). You log in with **production accounts and passwords**. `backups/*.sql` is gitignored—do not commit dumps.

To use an existing server backup instead of a live dump:

```bash
scp "${SERVER}:~/backups/mousexgene_*.sql" ./backups/
./scripts/restore_db_local.sh backups/mousexgene_YYYYMMDD_HHMMSS.sql
```

## Notes

- Keep `docker-compose.yml` for dev convenience.
- Use `.env.prod` with `DJANGO_DEBUG=0` for server testing.
- Ensure `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` are set correctly in production.
