#!/usr/bin/env bash

##############################################################################
# deploy-target: the host-side half of every deploy (one script for all services).
#
# Started by infra/deployment/deploy.sh over SSH, after the host has checked out the commit being
# deployed (so this file, the manifest and the service code are all from that commit):
#
#   deploy-target.sh <service> <sha> <color>      stdin: the service's secrets (see below)
#
# Everything service-specific comes from infra/services/<service>/deploy.yaml; the only code per
# service is an optional hook (infra/services/<service>/hooks/pre-start.sh). This is the
# `deploy-target` of docs/specs/deployer.md: the future deployer runs the same script.
#
# stdin carries the secrets, one per line: <destination path> TAB <owner uid:gid> TAB <base64 value>.
# They are read into memory first; nothing else in this script reads stdin.
#
# Steps:
#   1. Commit                5. Service hook (if any)
#   2. Config and secrets    6. Start, wait until healthy
#   3. certbot hook          7. Health by name over TLS
#   4. Build the image       8. Smoke test
##############################################################################

set -Eeuo pipefail
SERVICE="$1"; SHA="$2"; COLOR="${3:-0}"
SRC=/opt/steven-universe
MANIFEST="$SRC/infra/services/$SERVICE/deploy.yaml"
CFG="/etc/$SERVICE"                          # convention: config.env, credentials/, tls/ (owner 10001)

SECRETS="$(cat)"                             # the payload from deploy.sh, held in memory only
exec </dev/null                              # nothing below may read stdin (docker compose run would)

[ -f "$MANIFEST" ] || { echo "no manifest $MANIFEST at this commit" >&2; exit 1; }

# ---- The manifest, as shell variables (M_*) -------------------------------------------------
eval "$(python3 - "$MANIFEST" <<'PY'
import shlex, sys, yaml
m = yaml.safe_load(open(sys.argv[1]))
def out(name, value):
    print(f"{name}={shlex.quote('' if value is None else str(value))}")
smoke = m.get("smoke_test") or {}
cert = m.get("certificate") or {}
out("M_SERVICE", m["service"]); out("M_COMPOSE", m["compose"]); out("M_CODE", m.get("code"))
out("M_CONFIG", m.get("config")); out("M_URL", m.get("url")); out("M_HEALTH", m["health"])
out("M_SMOKE_SCRIPT", smoke.get("script")); out("M_SMOKE_SECRET", smoke.get("secret"))
out("M_CERT_NAME", cert.get("name")); out("M_CERT_HOOK", cert.get("hook"))
out("M_PRE_START", (m.get("hooks") or {}).get("pre_start"))
out("M_HOST_ONLY", " ".join(m.get("host_only") or []))
PY
)"
[ "$M_SERVICE" = "$SERVICE" ] || { echo "manifest says service '$M_SERVICE', expected '$SERVICE'" >&2; exit 1; }

TOTAL=$([ -n "$M_PRE_START" ] && echo 8 || echo 7)
# shellcheck source=lib.sh
source "$SRC/infra/deployment/lib.sh"
START=$SECONDS
SHORT="${SHA:0:7}"; SUBJECT="$(git -C "$SRC" log -1 --format=%s)"
SVC_DIR="$SRC/$(dirname "$M_COMPOSE")"
cd "$SVC_DIR"
CID="$(docker compose ps -q "$SERVICE" 2>/dev/null || true)"
PREV="$([ -n "$CID" ] && docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CID" 2>/dev/null || true)"

##############################################################################
# Step 1: the commit (already checked out by deploy.sh's SSH command)
##############################################################################
step "Commit"
ok "$SHORT: $SUBJECT"
if [ -z "$PREV" ]; then info "first deploy (nothing running yet)"
elif [ "$PREV" = "$SHA" ]; then info "same commit as the running container (redeploy)"
else info "running now: ${PREV:0:7}  ->  deploying: $SHORT"; fi

##############################################################################
# Step 2: config.env, secrets from OpenBao, host-only files, certificate
##############################################################################
step "Config and secrets"
install -d -m 700 -o 10001 -g 10001 "$CFG"
if [ -n "$M_CONFIG" ]; then
    install -m 600 -o 10001 -g 10001 "$SRC/$M_CONFIG" "$CFG/config.env"
    ok "config.env from $M_CONFIG"
fi

# Secrets: write each one atomically (temp file in the same directory, then rename), mode 600.
COUNT=0; CHANGED=0
while IFS=$'\t' read -r dest owner b64; do
    [ -n "${dest:-}" ] || continue
    dir="$(dirname "$dest")"
    [ -d "$dir" ] || install -d -m 700 -o "${owner%%:*}" -g "${owner##*:}" "$dir"
    tmp="$(mktemp "$dest.XXXXXX")"
    printf '%s' "$b64" | base64 -d >"$tmp"
    chown "$owner" "$tmp"; chmod 600 "$tmp"
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        rm -f "$tmp"; info "$dest: unchanged"
    else
        mv "$tmp" "$dest"; CHANGED=$((CHANGED + 1)); info "$dest: written"
    fi
    COUNT=$((COUNT + 1))
done <<<"$SECRETS"
unset SECRETS
ok "$COUNT secret(s) from OpenBao ($CHANGED changed)"

for p in $M_HOST_ONLY; do
    [ -e "$p" ] || die "Host-only path $p is missing (created on this host, never delivered)."
done
[ -z "$M_HOST_ONLY" ] || ok "host-only files present: $M_HOST_ONLY"

[ -s "$CFG/tls/cert.pem" ] && [ -s "$CFG/tls/key.pem" ] || die "No certificate in $CFG/tls (certbot; see the service README)."
CERT_END="$(openssl x509 -in "$CFG/tls/cert.pem" -noout -enddate | cut -d= -f2)"
CERT_DAYS=$(( ($(date -d "$CERT_END" +%s) - $(date +%s)) / 86400 ))
CERT_DATE="$(date -d "$CERT_END" +%Y-%m-%d)"
if [ "$CERT_DAYS" -lt 0 ]; then die "Certificate EXPIRED on $CERT_DATE. Run: certbot renew"
elif [ "$CERT_DAYS" -lt 21 ]; then warn "Certificate expires in $CERT_DAYS days ($CERT_DATE); check: certbot renew --dry-run"
else ok "certificate valid until $CERT_DATE ($CERT_DAYS days left)"; fi

##############################################################################
# Step 3: certbot hook from this commit (copies renewed certificates for uid 10001)
##############################################################################
step "certbot hook"
if [ -n "$M_CERT_HOOK" ]; then
    install -m 755 "$SRC/$M_CERT_HOOK" "/usr/local/sbin/$SERVICE-cert-hook"
    ok "/usr/local/sbin/$SERVICE-cert-hook"
else
    info "none declared"
fi

##############################################################################
# Step 4: build the image (labelled with the commit; the running container is untouched)
##############################################################################
step "Building image"
quiet env REVISION="$SHA" docker compose build "$SERVICE"
IMAGE="$(docker compose config --images "$SERVICE")"
ok "Built $IMAGE ($(docker image inspect -f '{{.Size}}' "$IMAGE" | numfmt --to=iec), revision $SHORT)"

##############################################################################
# Step 5 (only if declared): the service's own pre-start steps
##############################################################################
if [ -n "$M_PRE_START" ]; then
    # Sourced: the hook uses step/ok/die and $SRC $SVC_DIR $CFG $SHA $SHORT $IMAGE.
    # shellcheck disable=SC1090
    source "$SRC/$M_PRE_START"
fi

##############################################################################
# Start and wait until Docker's health check passes
##############################################################################
step "Starting container"
quiet env REVISION="$SHA" docker compose up -d "$SERVICE"
CID="$(docker compose ps -q "$SERVICE")"
info "waiting for the health check (up to 60 s)..."
STATUS=starting
for i in $(seq 1 30); do
    STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CID")"
    [ "$STATUS" = healthy ] && break
    sleep 2
done
if [ "$STATUS" != healthy ]; then
    docker compose logs --tail 40 "$SERVICE" >&2
    die "Container is '$STATUS' after 60 s (logs above)."
fi
ok "healthy after $(( i * 2 )) s"

##############################################################################
# Health the way callers reach it: DNS, port 443, certificate verified
##############################################################################
step "Checking $M_HEALTH"
HEALTH="$(curl -fsS "$M_HEALTH")" || die "Not reachable by name over TLS (DNS, port, certificate, or a dependency)."
ok "reachable by name, certificate verified: $(printf '%s' "$HEALTH" | cut -c1-120)"
RUNNING="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CID")"
[ "$RUNNING" = "$SHA" ] || die "Container reports revision ${RUNNING:0:7}, expected $SHORT."

##############################################################################
# Smoke test: the service's script, in the new image, with host networking
##############################################################################
step "Smoke test"
if [ -n "$M_SMOKE_SCRIPT" ]; then
    [ -s "$M_SMOKE_SECRET" ] || die "Missing $M_SMOKE_SECRET (it should have been delivered from OpenBao in step 2)."
    if SMOKE_OUT="$(docker run --rm --network host --read-only --user 0 --cap-drop ALL \
            --security-opt no-new-privileges:true \
            -v "$M_SMOKE_SECRET:/t/secret:ro" -v "$SRC/$M_SMOKE_SCRIPT:/t/smoke.py:ro" \
            --entrypoint python "$IMAGE" /t/smoke.py 2>&1)"; then
        printf '%s\n' "$SMOKE_OUT" | grep -E '^(PASS|FAIL)' | sed 's/^/  /'
        ok "smoke test passed ($(printf '%s\n' "$SMOKE_OUT" | grep -c '^PASS') checks)"
    else
        printf '%s\n' "$SMOKE_OUT" | sed 's/^/  /' >&2
        die "Smoke test FAILED. The new version is running; roll back with: infra/deployment/deploy.sh $SERVICE ${PREV:0:7}"
    fi
else
    info "none declared"
fi

quiet docker image prune -f --filter "label=org.opencontainers.image.title=$SERVICE"

##############################################################################
# Summary
##############################################################################
printf '\n%s========================================%s\n' "$GREEN" "$NC"
printf '%s%s deployed in %d s%s\n' "$GREEN" "$SERVICE" "$(( SECONDS - START ))" "$NC"
printf '%s========================================%s\n' "$GREEN" "$NC"
[ -z "$M_URL" ] || printf 'URL:          %s%s%s\n' "$BLUE" "$M_URL" "$NC"
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf 'Commit:       %s%s%s %s  (was %s)\n' "$BLUE" "$SHORT" "$NC" "$SUBJECT" "${PREV:0:7}"
else
    printf 'Commit:       %s%s%s %s\n' "$BLUE" "$SHORT" "$NC" "$SUBJECT"
fi
printf 'Secrets:      %s from OpenBao (%s changed)\n' "$COUNT" "$CHANGED"
printf 'Certificate:  valid until %s (%s days)\n' "$CERT_DATE" "$CERT_DAYS"
printf '\nLogs:         docker compose -f %s logs -f %s   (on the host)\n' "$SRC/$M_COMPOSE" "$SERVICE"
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf 'Roll back:    infra/deployment/deploy.sh %s %s   (from the Mac)\n' "$SERVICE" "${PREV:0:7}"
else
    printf 'Roll back:    infra/deployment/deploy.sh %s <older commit sha>   (from the Mac)\n' "$SERVICE"
fi
