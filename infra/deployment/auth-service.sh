#!/usr/bin/env bash
# Deploy auth-service to its LXC (CT 102) by hand. Run on the Mac, from any directory.
#
#   infra/deployment/auth-service.sh             deploy origin/main
#   infra/deployment/auth-service.sh <ref>       deploy a pushed branch, tag or commit sha
#
# The host builds from a clean checkout of a PUSHED commit (never this working copy), so what
# runs is always a real commit. AUTH_HOST picks the SSH host (default: auth-host in ~/.ssh/config).
#
# Each run: check out <ref> in /opt/steven-universe; make sure /etc/auth-service exists (owned
# by the container user 10001); require the TLS certificate; validate and install policy.yaml
# from that commit; create the first signing key if there is none; build; restart; wait until
# healthy; check /health over TLS by name. Client secrets are never created here
# (python/services/auth-service/scripts/add-client.sh). Spec: docs/specs/auth-service.md.
set -euo pipefail

AUTH_HOST="${AUTH_HOST:-auth-host}"
REF="${1:-main}"
REPO_URL="https://github.com/liustev6/steven-universe.git"

if [[ ! "$REF" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "invalid ref: $REF" >&2
  exit 2
fi

ssh -o BatchMode=yes "$AUTH_HOST" bash -s -- "$REF" "$REPO_URL" <<'REMOTE'
set -euo pipefail
REF="$1"; REPO_URL="$2"
SRC=/opt/steven-universe
SVC="$SRC/python/services/auth-service"
CFG=/etc/auth-service
ISSUER_HOST=auth.lan.liustev6.ca
step() { printf '\n==> %s\n' "$*"; }

step "Checking out $REF"
[ -d "$SRC/.git" ] || git clone --quiet --filter=blob:none "$REPO_URL" "$SRC"
git -C "$SRC" fetch --quiet --prune origin
if git -C "$SRC" rev-parse --verify --quiet "origin/$REF^{commit}" >/dev/null; then
  TARGET="origin/$REF"
else
  TARGET="$REF"
fi
git -C "$SRC" -c advice.detachedHead=false checkout --quiet --force --detach "$TARGET"
SHA="$(git -C "$SRC" rev-parse HEAD)"
echo "commit $SHA ($(git -C "$SRC" log -1 --format=%s))"

step "Config directory $CFG (owner 10001, mode 700)"
install -d -m 700 -o 10001 -g 10001 "$CFG" "$CFG/keys" "$CFG/clients" "$CFG/tls"
if [ ! -s "$CFG/tls/cert.pem" ] || [ ! -s "$CFG/tls/key.pem" ]; then
  echo "missing $CFG/tls/cert.pem or key.pem: get the certificate first (auth-service README, step 5)" >&2
  exit 1
fi

step "Admin CLI -> /usr/local/bin/auth-service-admin"
install -m 755 "$SVC/bin/auth-service-admin" /usr/local/bin/auth-service-admin

step "Building image"
cd "$SVC"
REVISION="$SHA" docker compose build --quiet auth-service

step "Policy"
install -m 600 -o 10001 -g 10001 "$SVC/policy.yaml" "$CFG/policy.yaml.new"
if ! auth-service-admin check-policy --file "$CFG/policy.yaml.new"; then
  rm -f "$CFG/policy.yaml.new"
  echo "policy.yaml at $SHA is invalid; nothing changed" >&2
  exit 1
fi
if [ -f "$CFG/policy.yaml" ] && cmp -s "$CFG/policy.yaml" "$CFG/policy.yaml.new"; then
  rm -f "$CFG/policy.yaml.new"
  echo "unchanged"
else
  [ -f "$CFG/policy.yaml" ] && diff -u "$CFG/policy.yaml" "$CFG/policy.yaml.new" || true
  mv "$CFG/policy.yaml.new" "$CFG/policy.yaml"   # atomic; a running service reloads it
  echo "installed"
fi

step "Signing key"
if [ -s "$CFG/keys/active" ]; then
  echo "active: $(cat "$CFG/keys/active")"
else
  KID="$(auth-service-admin new-key | awk '{print $2}')"
  auth-service-admin promote-key "$KID"
fi

step "Starting"
REVISION="$SHA" docker compose up -d auth-service
CID="$(docker compose ps -q auth-service)"
for _ in $(seq 1 30); do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CID")"
  [ "$STATUS" = healthy ] && break
  sleep 2
done
echo "container: $STATUS"
if [ "$STATUS" != healthy ]; then
  docker compose logs --tail 40 auth-service >&2
  exit 1
fi

step "Health over TLS (https://$ISSUER_HOST:8443/health, certificate verified)"
curl -fsS "https://$ISSUER_HOST:8443/health"
echo
echo "running $(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CID")"
docker image prune -f --filter "label=org.opencontainers.image.title=auth-service" >/dev/null
REMOTE
