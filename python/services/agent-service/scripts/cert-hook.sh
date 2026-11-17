#!/bin/sh
# certbot deploy hook for agent-service (installed on CT 107 as /usr/local/sbin/agent-service-cert-hook).
#
# certbot keeps certificates root-only in /etc/letsencrypt; the container runs as uid 10001.
# After each issue or renewal this copies the certificate and key into /etc/agent-service/tls/
# (owner 10001, mode 600) and restarts the container if it is running, since uvicorn reads the
# certificate only at start.
#
# Registered with: certbot certonly ... --deploy-hook /usr/local/sbin/agent-service-cert-hook
# (certbot stores it in the renewal config, so renewals run it too).
set -eu

LINEAGE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/agent.lan.liustev6.ca}"
TLS=/etc/agent-service/tls
COMPOSE=/opt/steven-universe/python/services/agent-service/compose.yaml

install -d -m 700 -o 10001 -g 10001 /etc/agent-service "$TLS"
install -m 600 -o 10001 -g 10001 "$LINEAGE/fullchain.pem" "$TLS/cert.pem.new"
install -m 600 -o 10001 -g 10001 "$LINEAGE/privkey.pem" "$TLS/key.pem.new"
mv "$TLS/cert.pem.new" "$TLS/cert.pem"
mv "$TLS/key.pem.new" "$TLS/key.pem"

if [ -f "$COMPOSE" ] && [ -n "$(docker compose -f "$COMPOSE" ps -q agent-service 2>/dev/null)" ]; then
  docker compose -f "$COMPOSE" restart agent-service
fi
logger -t agent-service-cert-hook "installed certificate from $LINEAGE"
