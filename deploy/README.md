# Production deployment notes

## Static files (nginx + web)

`web` and `nginx` both mount the host directory `./staticfiles` at `/app/staticfiles`, matching Django `STATIC_ROOT`. After building or changing static assets, run `collectstatic` inside `web` so nginx serves the same files.

### Suggested steps on the server

```bash
rm -rf staticfiles/*
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx
```

Adjust paths if your project root or compose file location differs.
