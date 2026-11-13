#!/bin/sh
# certbot deploy hook for sandbox-runner (installed on VM 200 as /usr/local/sbin/sandbox-runner-cert-hook).
#
# certbot keeps certificates root-only in /etc/letsencrypt. After each issue or renewal this copies
# the certificate and key into /etc/sandbox-runner/tls/ and restarts the runner if it is running,
# since uvicorn reads the certificate only at start. (A restart ends running sessions; renewals
# happen every ~60 days.)
#
# Registered with: certbot certonly ... --deploy-hook /usr/local/sbin/sandbox-runner-cert-hook
set -eu

LINEAGE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/sandbox-runner.lan.liustev6.ca}"
TLS=/etc/sandbox-runner/tls
COMPOSE=/opt/steven-universe/python/services/sandbox-runner/compose.yaml

install -d -m 700 -o 10001 -g 10001 /etc/sandbox-runner "$TLS"
install -m 600 -o 10001 -g 10001 "$LINEAGE/fullchain.pem" "$TLS/cert.pem.new"
install -m 600 -o 10001 -g 10001 "$LINEAGE/privkey.pem" "$TLS/key.pem.new"
mv "$TLS/cert.pem.new" "$TLS/cert.pem"
mv "$TLS/key.pem.new" "$TLS/key.pem"

if [ -f "$COMPOSE" ] && [ -n "$(docker compose -f "$COMPOSE" ps -q sandbox-runner 2>/dev/null)" ]; then
  docker compose -f "$COMPOSE" restart sandbox-runner
fi
logger -t sandbox-runner-cert-hook "installed certificate from $LINEAGE"
