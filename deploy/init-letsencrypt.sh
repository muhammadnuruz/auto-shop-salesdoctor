#!/bin/sh
# First-time TLS bootstrap: nginx needs a certificate to start, certbot needs nginx to answer
# the ACME challenge. So: dummy self-signed cert -> start nginx -> real cert -> reload.
# Requires DOMAIN and LETSENCRYPT_EMAIL in .env; DNS of DOMAIN must point to this server.
# Set LETSENCRYPT_STAGING=1 for a dry run against the staging CA (avoids rate limits).
set -eu
cd "$(dirname "$0")/.."
# read only what we need (never `source` .env: values may contain shell metacharacters)
env_get() { sed -n "s/^$1=//p" .env | tail -n 1 | sed 's/[[:space:]]*#.*$//; s/^"//; s/"$//'; }
DOMAIN=$(env_get DOMAIN)
LETSENCRYPT_EMAIL=$(env_get LETSENCRYPT_EMAIL)
: "${DOMAIN:?set DOMAIN in .env}"
: "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL in .env}"
DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile web"
live="/etc/letsencrypt/live/$DOMAIN"

echo "### dummy certificate for $DOMAIN"
$DC run --rm --entrypoint sh certbot -c "mkdir -p '$live' && \
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 -subj '/CN=localhost' \
  -keyout '$live/privkey.pem' -out '$live/fullchain.pem'"

echo "### starting nginx"
$DC up -d --build web

echo "### requesting the real certificate"
$DC run --rm --entrypoint sh certbot -c "rm -rf '/etc/letsencrypt/live/$DOMAIN' \
  '/etc/letsencrypt/archive/$DOMAIN' '/etc/letsencrypt/renewal/$DOMAIN.conf'"
staging=""
[ "${LETSENCRYPT_STAGING:-0}" = "1" ] && staging="--staging"
if ! $DC run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
  $staging -d "$DOMAIN" --email "$LETSENCRYPT_EMAIL" --rsa-key-size 4096 \
  --agree-tos --no-eff-email --non-interactive; then
  echo "### certbot failed (DNS not pointing here yet?): restoring the dummy certificate" >&2
  $DC run --rm --entrypoint sh certbot -c "mkdir -p '$live' && \
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 -subj '/CN=localhost' \
    -keyout '$live/privkey.pem' -out '$live/fullchain.pem'"
  exit 1
fi

echo "### reloading nginx"
$DC exec web nginx -s reload
echo "done: https://$DOMAIN"
