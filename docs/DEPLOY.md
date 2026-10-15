# Production deployment

Single server with Docker (Compose v2.24+). Everything runs from this repository:
`docker-compose.yml` + the `docker-compose.prod.yml` overlay.

## 1. Server

- Ubuntu 22.04/24.04, 2–4 vCPU, 4–8 GB RAM, 40 GB disk (dumps are small: ~1 MB/1000 orders).
- DNS `A` record of your domain → the server. Open ports 80 and 443 only.
- Install Docker Engine + the compose plugin. Create a non-root deploy user in the `docker` group.

## 2. Configure

```bash
git clone <repo> /opt/auto-shop && cd /opt/auto-shop
cp .env.prod.example .env && chmod 600 .env
# fill in DOMAIN, LETSENCRYPT_EMAIL, ALLOWED_ORIGINS, APP_SECRET_KEY, TOTP_ENC_KEY,
# POSTGRES_PASSWORD (+ the same password inside DATABASE_URL), SD_LOGIN, SD_PASSWORD
```

The app validates its configuration at startup and refuses to start in `production` with
weak or missing secrets, a non-https origin, or `COOKIE_SECURE=false`.

## 3. First start

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile web"
./deploy/init-letsencrypt.sh        # dummy cert -> nginx -> real Let's Encrypt cert
$DC up -d --build                   # migrate (one-shot) -> api, worker, web, certbot, backup
$DC run --rm api python -m app.cli create-superadmin --username admin   # prompts for password
```

Open `https://<DOMAIN>/admin`, log in, enable 2FA (Profile), then create links.
The worker runs a full Sales Doctor sync at startup. Watch it on the **Синхронизация** page.

## 4. What runs

| Service | Role | Health / restart |
|---|---|---|
| `migrate` | `alembic upgrade head`, then exits; api/worker wait for it | one-shot |
| `api` | FastAPI, `WEB_CONCURRENCY` uvicorn processes, no host port | `/health/ready` (DB + Redis). 3 failures in a row → restart (`app.healthcheck`) |
| `worker` | arq: sync dispatcher, order reconcile, status sync, Telegram notifier, retention | arq health key; 3 failures → restart |
| `web` | nginx: SPA, `/api` proxy, TLS, HSTS, CSP, rate limit 30 r/s per IP | `/healthz` |
| `certbot` | renews certificates every 12 h (nginx reloads every 6 h) | — |
| `backup` | daily `pg_dump` at `BACKUP_HOUR` (Tashkent), restore test of every dump, prune after `BACKUP_RETENTION_DAYS` | unhealthy if no verified dump in 26 h |
| `db`, `redis` | Postgres 16, Redis 7 (AOF) | built-in |

## 5. Updates

```bash
git pull
APP_RELEASE=$(git rev-parse --short HEAD) $DC up -d --build   # migrations run first, automatically
```

Migrations are forward-only. To roll back, deploy the previous commit. A migration is only
reverted after restoring the last dump.

## 6. Backups and restore

- Dumps: `${BACKUP_DIR}/autoshop-YYYYmmdd-HHMMSS.dump` (custom format). A `.verified` marker
  appears once that dump has been restored into a scratch database and checked.
- Copy `BACKUP_DIR` off the server (rsync / object storage) — a dump on the same disk does not
  protect against losing the server.
- Run a backup now: `$DC exec backup sh /scripts/backup.sh`
- **Restore** (destroys current data):

```bash
$DC stop api worker
$DC exec backup sh -c 'dropdb --force "$PGDATABASE" && createdb "$PGDATABASE" && \
  pg_restore --no-owner --exit-on-error -d "$PGDATABASE" /backups/<file>.dump'
$DC up -d
```

## 7. Logs, errors, monitoring

- All services log JSON lines to stdout (secrets redacted, phone numbers masked), each API
  request tagged with `request_id` (also returned as `X-Request-ID`). Docker rotates the
  logs (20 MB × 10 per container): `$DC logs -f api worker`.
- Shipping: point any Docker log collector (Vector, Promtail/Loki, Fluent Bit, Grafana Agent)
  at the json-file logs. Fields to index: `level`, `event`, `logger`, `request_id`.
- Errors: set `SENTRY_DSN` to send backend and worker exceptions to Sentry (no PII,
  no bodies, cookies, auth headers or query strings). Browser errors go to
  `POST /api/client-errors` (same origin, rate limited). There they are logged as
  `client_error` and forwarded to Sentry.
- Telegram alerts (new orders, failed / unknown / parked orders, sync failures): admin panel
  → **Настройки** → Telegram.
- Useful checks: `curl -fsS https://<DOMAIN>/health`; `$DC ps` (health column).

## 8. CI

`.github/workflows/ci.yml`:

- **Backend:** ruff, format, mypy, pytest with Postgres/Redis services.
- **Frontend:** lint, typecheck, prettier, vitest, build.
- **Docker:** builds both images and renders the prod compose.
- **E2E:** Playwright against the docker stack with the fake Sales Doctor, plus a short load
  test.

## 9. Shared server with an existing host nginx

When ports 80/443 already belong to a host-level nginx (other sites on the same server),
do not start the container TLS proxy. Add the `docker-compose.host-proxy.yml` overlay: the
`web` container then serves plain HTTP on `127.0.0.1:${WEB_HOST_PORT:-8095}` and the
container `certbot` is disabled. The host nginx terminates TLS with the host certbot.

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host-proxy.yml --profile web"
$DC up -d --build
sed 's/DOMAIN/<your domain>/' deploy/nginx/host-site.conf.example > /etc/nginx/sites-available/auto-shop
ln -s /etc/nginx/sites-available/auto-shop /etc/nginx/sites-enabled/ && nginx -t && nginx -s reload
certbot --nginx -d <your domain> --redirect
```

The host site passes the client address in `X-Real-IP`; the container nginx forwards it as
`X-Forwarded-For` (overwritten, never appended), which the API trusts from `TRUSTED_PROXIES`.
Always pass the same `-f` set (and run tests under another project name, e.g.
`docker compose -p autoshop-test --profile test run --rm test pytest`): running the dev
compose under the production project name recreates the network and stops `db`.
