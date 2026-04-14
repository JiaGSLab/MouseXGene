# MouseXGene

MouseXGene is a production-oriented Django skeleton for lightweight mouse colony management in a single academic lab.

## Stack

- Python 3.11
- Django
- PostgreSQL
- Docker / Docker Compose
- Django Admin
- pandas + openpyxl (future Excel import)
- WeasyPrint (future PDF export)

## Project apps

- `core`
- `users`
- `colony`
- `breeding`
- `genotypes`

## Quick start (local with Docker)

1. Copy env file:

   ```bash
   cp .env.example .env
   ```

2. Build and start:

   ```bash
   docker compose up --build
   ```

3. Apply migrations:

   ```bash
   docker compose exec web python manage.py makemigrations
   docker compose exec web python manage.py migrate
   ```

4. Create an admin user:

   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

5. Access:
   - Homepage: `http://localhost:8000/`
   - Admin: `http://localhost:8000/admin/`

## Development notes

- Database is PostgreSQL (no SQLite fallback).
- Settings are environment-driven through `config/settings.py`.
- Initial model stubs are included for:
  - `Mouse`, `Cage`, `CageMembership`
  - `Breeding`, `Litter`
  - `Gene`, `Allele`, `MouseGenotype`
  - `Project`, `AuditLog`
