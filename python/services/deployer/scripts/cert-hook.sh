#!/bin/sh
# certbot deploy hook for deployer (installed on CT 105 as /usr/local/sbin/deployer-cert-hook).
#
# certbot keeps certificates root-only in /etc/letsencrypt; the container runs as uid 10001.
# After each issue or renewal this copies the certificate and key into /etc/deployer/tls/
# (owner 10001, mode 600) and restarts the container if it is running, since uvicorn reads the
# certificate only at start.
#
# Registered with: certbot certonly ... --deploy-hook /usr/local/sbin/deployer-cert-hook
# (certbot stores it in the renewal config, so renewals run it too).
set -eu

LINEAGE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/deploy.lan.liustev6.ca}"
TLS=/etc/deployer/tls
COMPOSE=/opt/steven-universe/python/services/deployer/compose.yaml

install -d -m 700 -o 10001 -g 10001 /etc/deployer "$TLS"
install -m 600 -o 10001 -g 10001 "$LINEAGE/fullchain.pem" "$TLS/cert.pem.new"
install -m 600 -o 10001 -g 10001 "$LINEAGE/privkey.pem" "$TLS/key.pem.new"
mv "$TLS/cert.pem.new" "$TLS/cert.pem"
mv "$TLS/key.pem.new" "$TLS/key.pem"

if [ -f "$COMPOSE" ] && [ -n "$(docker compose -f "$COMPOSE" ps -q deployer 2>/dev/null)" ]; then
  docker compose -f "$COMPOSE" restart deployer
fi
logger -t deployer-cert-hook "installed certificate from $LINEAGE"
