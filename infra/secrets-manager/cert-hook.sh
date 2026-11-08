#!/bin/sh
# certbot deploy hook for the secrets-manager (installed on CT 104 as /usr/local/sbin/openbao-cert-hook).
#
# certbot keeps certificates root-only in /etc/letsencrypt; the OpenBao container runs as uid 10001.
# After each issue or renewal this copies the certificate and key into /etc/openbao/tls/ (owner
# 10001, mode 600). OpenBao reloads them by itself (tls_auto_reload) and on SIGHUP, sent below as a
# fallback. It is NEVER restarted here: a restart would seal it.
#
# Registered with: certbot certonly ... --deploy-hook /usr/local/sbin/openbao-cert-hook
set -eu

LINEAGE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/bao.lan.liustev6.ca}"
TLS=/etc/openbao/tls
CONTAINER=secrets-manager-openbao-1

install -d -m 700 -o 10001 -g 10001 /etc/openbao "$TLS"
install -m 600 -o 10001 -g 10001 "$LINEAGE/fullchain.pem" "$TLS/cert.pem.new"
install -m 600 -o 10001 -g 10001 "$LINEAGE/privkey.pem" "$TLS/key.pem.new"
mv "$TLS/cert.pem.new" "$TLS/cert.pem"
mv "$TLS/key.pem.new" "$TLS/key.pem"

if [ -n "$(docker ps -q --filter "name=^${CONTAINER}$" 2>/dev/null)" ]; then
  docker kill --signal=HUP "$CONTAINER" >/dev/null   # reload certificates; stays unsealed
fi
logger -t openbao-cert-hook "installed certificate from $LINEAGE"
