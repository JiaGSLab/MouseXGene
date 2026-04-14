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

## Notes

- Keep `docker-compose.yml` for dev convenience.
- Use `.env.prod` with `DJANGO_DEBUG=0` for server testing.
- Ensure `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` are set correctly in production.
