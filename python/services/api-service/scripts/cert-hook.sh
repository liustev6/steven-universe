#!/bin/sh
# certbot deploy hook for api-service (installed on CT 106 as /usr/local/sbin/api-service-cert-hook).
#
# certbot keeps certificates root-only in /etc/letsencrypt; the container runs as uid 10001.
# After each issue or renewal this copies the certificate and key into /etc/api-service/tls/
# (owner 10001, mode 600) and restarts the container if it is running, since uvicorn reads the
# certificate only at start.
#
# Registered with: certbot certonly ... --deploy-hook /usr/local/sbin/api-service-cert-hook
# (certbot stores it in the renewal config, so renewals run it too).
set -eu

LINEAGE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/api.lan.liustev6.ca}"
TLS=/etc/api-service/tls
COMPOSE=/opt/steven-universe/python/services/api-service/compose.yaml

install -d -m 700 -o 10001 -g 10001 /etc/api-service "$TLS"
install -m 600 -o 10001 -g 10001 "$LINEAGE/fullchain.pem" "$TLS/cert.pem.new"
install -m 600 -o 10001 -g 10001 "$LINEAGE/privkey.pem" "$TLS/key.pem.new"
mv "$TLS/cert.pem.new" "$TLS/cert.pem"
mv "$TLS/key.pem.new" "$TLS/key.pem"

if [ -f "$COMPOSE" ] && [ -n "$(docker compose -f "$COMPOSE" ps -q api-service 2>/dev/null)" ]; then
  docker compose -f "$COMPOSE" restart api-service
fi
logger -t api-service-cert-hook "installed certificate from $LINEAGE"
