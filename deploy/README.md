# Production deployment (Docker Compose + Nginx + Let’s Encrypt)

## Layout

- **`web`**: Gunicorn; mounts `./staticfiles` → `/app/staticfiles` (same as `STATIC_ROOT`).
- **`nginx`**: Terminates TLS; proxies to `web:8000`; serves `/static/` from `/app/staticfiles`.
- **ACME webroot**: Host directory `./acme-challenge` → container `/var/www/certbot` (read-only in container; Certbot on the **host** writes challenges here).
- **Certificates**: Host `/etc/letsencrypt` → container `/etc/letsencrypt` (read-only).

## DNS and firewall

- `jialabmouse.top` and `www.jialabmouse.top` **A records** → your server’s public IP.
- Open inbound **TCP 80** and **TCP 443** on the cloud security group / firewall.

## Deploy code from your Mac (rsync)

**Important:** `web` mounts the project directory (`.:/app`). Rsync only updates files on the **host**; you must run **`apply_on_server.sh`** (migrate + collectstatic + restart `web`) or the site keeps serving old code baked into an earlier image.

On your Mac:

```bash
cd /path/to/MouseXGene
chmod +x scripts/rsync_to_server.sh scripts/apply_on_server.sh
./scripts/rsync_to_server.sh
ssh ubuntu@YOUR_SERVER 'cd ~/apps/MouseXGene && ./scripts/apply_on_server.sh'
```

Change host if needed: `SERVER=ubuntu@1.2.3.4 ./scripts/rsync_to_server.sh`

After changing `requirements.txt` or `Dockerfile`, rebuild on the server:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build web
```

## Uploaded files (strain line PDFs)

`web` mounts host `./media` → `/app/media` in the container. Back up this directory with your database. PDFs are served through the app (login required), not as public static files.

Run `mkdir -p media` on the server once. Upload PDFs in the UI after deploy; they are stored under `./media/strain_lines/`.

## Static files

After code or asset changes:

```bash
cd ~/apps/MouseXGene
./scripts/apply_on_server.sh
```

Or manually:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py migrate --noinput
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d web
docker compose -f docker-compose.prod.yml --env-file .env.prod exec nginx nginx -s reload
```

## Django behind HTTPS (`.env.prod`)

After HTTPS is live, set (adjust if needed):

```env
DJANGO_ALLOWED_HOSTS=jialabmouse.top,www.jialabmouse.top
DJANGO_CSRF_TRUSTED_ORIGINS=https://jialabmouse.top,https://www.jialabmouse.top
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SECURE_PROXY_SSL_HEADER=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_SECURE_HSTS_SECONDS=604800
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=false
DJANGO_SECURE_HSTS_PRELOAD=false
```

Start HSTS with a short value such as `604800` seconds (7 days). Only enable
`DJANGO_SECURE_HSTS_PRELOAD=true` after you are certain every subdomain is
permanently HTTPS-ready.

Restart `web` after editing `.env.prod`:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d web
```

---

## Let’s Encrypt (Certbot on the Ubuntu host)

Certbot runs **on the host**, not inside Docker. Nginx in Docker serves the HTTP-01 challenge from `./acme-challenge`.

### Why a bootstrap config?

`deploy/nginx/default.conf` references `/etc/letsencrypt/live/jialabmouse.top/...`, which **do not exist** until the first certificate is issued. On a brand-new server, temporarily use **`deploy/nginx/default-bootstrap.conf`** (HTTP only, ACME + app) so Nginx can start; after `certbot` succeeds, switch back to the repo’s HTTPS **`default.conf`**.

### 1) First-time bootstrap (temporary HTTP-only)

On the server, from the project root:

```bash
cd ~/apps/MouseXGene
mkdir -p acme-challenge
cp deploy/nginx/default-bootstrap.conf deploy/nginx/default.conf
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d nginx web db
```

Verify `http://jialabmouse.top` loads (HTTP) and that files under `acme-challenge/` are visible to Nginx (container mapping is already in `docker-compose.prod.yml`).

### 2) Obtain the first certificate (webroot)

Install Certbot if needed (`sudo apt install certbot`). Issue certs using the **host path** that maps to `/var/www/certbot` in the container:

```bash
sudo certbot certonly --webroot \
  -w /home/ubuntu/apps/MouseXGene/acme-challenge \
  -d jialabmouse.top -d www.jialabmouse.top \
  --email your-email@example.com \
  --agree-tos --non-interactive
```

(Replace `/home/ubuntu/apps/MouseXGene` if your project path differs.)

### 3) Switch Nginx to the HTTPS configuration

Restore the repository’s TLS config (do **not** leave the bootstrap file in place):

```bash
cd ~/apps/MouseXGene
git checkout deploy/nginx/default.conf
# If you don’t use git on the server, copy the HTTPS default.conf from your rsync / repo checkout instead.
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d nginx
docker compose -f docker-compose.prod.yml --env-file .env.prod exec nginx nginx -t
docker compose -f docker-compose.prod.yml --env-file .env.prod exec nginx nginx -s reload
```

Visit `https://jialabmouse.top` and confirm the browser shows a valid certificate.

### 4) Automatic renewal + reload Nginx container

Certbot installs a **systemd timer** (or cron) on Ubuntu that runs `certbot renew` twice daily. Renewals only occur when certificates are within the renewal window.

Add a **deploy hook** so Nginx picks up renewed certs:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-mousexgene-nginx.sh >/dev/null <<'EOF'
#!/bin/sh
docker exec mousexgene_nginx_prod nginx -t && docker exec mousexgene_nginx_prod nginx -s reload
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-mousexgene-nginx.sh
```

Test renewal **without** changing certificates:

```bash
sudo certbot renew --dry-run
```

You should see the deploy hook run at the end of a successful simulated renewal.

### 5) Troubleshooting

- **`nginx: [em] host not found in upstream`** — ensure `web` container is up (`docker compose ps`).
- **Permission denied on webroot** — ensure Certbot’s `-w` path is exactly the host directory mounted as `./acme-challenge`.
- **403 / challenge fails** — check security group allows port 80 from the internet during issuance.

---

## Legacy manual certificates (Tencent zip, etc.)

If you previously mounted certs from `deploy/ssl/`, that flow is optional and separate. This README assumes **Let’s Encrypt** paths under `/etc/letsencrypt`.
