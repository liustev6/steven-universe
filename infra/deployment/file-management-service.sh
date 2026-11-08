#!/usr/bin/env bash

##############################################################################
# file-management Deployment Script (manual, from the Mac)
#
# Deploys file-management to its LXC (CT 103, 192.168.50.66) with Docker Compose.
# This script runs on the Mac, but the work happens on the host: it opens ONE
# SSH session and sends the block between <<'REMOTE' and REMOTE to bash there.
#
# The host builds from a clean checkout of a PUSHED commit (never this Mac's
# working copy), so what runs is always a real commit. Push first, then deploy.
#
# Steps on the host:
#   1. Check out the commit           5. Start container, wait until healthy
#   2. Config: keys + certificate     6. Check /health by name over TLS
#   3. Install the certbot hook       7. Smoke test as the `smoke-test` client
#   4. Build the image
#
# Prerequisites (one-time, see python/services/file-management-service/README.md):
# - `files-host` in ~/.ssh/config (or set FILES_HOST)
# - on the host: Docker + Compose; /etc/file-management/credentials/rustfs_{access,secret}_key
#   (the limited RustFS key, owner 10001); the certificate from certbot; and the smoke-test
#   client secret in /root/.secrets/smoke-test-client
#
# Usage:
#   infra/deployment/file-management-service.sh           # deploy main (latest pushed commit)
#   infra/deployment/file-management-service.sh <ref>     # a pushed branch, tag or sha (also: roll back)
##############################################################################

set -euo pipefail  # stop on errors, unset variables, and failures inside pipes

FILES_HOST="${FILES_HOST:-files-host}"
REF="${1:-main}"
REPO_URL="https://github.com/liustev6/steven-universe.git"   # public: no credentials needed

# Colors only when printing to a terminal (not when piped into a file or another command).
if [ -t 1 ]; then COLOR=1; else COLOR=0; fi
if [ "$COLOR" = 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
else
    RED=''; GREEN=''; BLUE=''; NC=''
fi

# The ref is passed to git on the host; allow only plain branch/tag/sha characters.
if [[ ! "$REF" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo -e "${RED}✗ Invalid ref: $REF${NC}" >&2
    exit 2
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}file-management Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Target host: ${BLUE}$FILES_HOST${NC}"
echo -e "Deploying:   ${BLUE}$REF${NC} (from GitHub)"

##############################################################################
# Everything below runs on the host (see auth-service.sh for why it's one
# function called with stdin from /dev/null, and why 'REMOTE' is quoted).
##############################################################################

ssh -o BatchMode=yes "$FILES_HOST" bash -s -- "$REF" "$REPO_URL" "$COLOR" <<'REMOTE'
set -Eeuo pipefail
deploy() {
REF="$1"; REPO_URL="$2"; COLOR="$3"
SRC=/opt/steven-universe
SVC="$SRC/python/services/file-management-service"
CFG=/etc/file-management                      # credentials/ and tls/, owner 10001
NAME=files.lan.liustev6.ca                    # DNS -> 192.168.50.66; the certificate's name
CONTAINER=file-management-file-management-1
SMOKE_SECRET=/root/.secrets/smoke-test-client

# ---- output helpers (same as auth-service.sh) -------------------------------
if [ "$COLOR" = 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi
STEP=0; TOTAL=7; STEP_NAME=""
step() { STEP=$((STEP + 1)); STEP_NAME="$*"; printf '\n%sStep %d/%d: %s%s\n' "$YELLOW" "$STEP" "$TOTAL" "$*" "$NC"; }
ok()   { printf '%s✓ %s%s\n' "$GREEN" "$*" "$NC"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '%s! %s%s\n' "$YELLOW" "$*" "$NC"; }
die()  { printf '\n%s✗ %s%s\n' "$RED" "$*" "$NC" >&2; exit 1; }
quiet() {
    local log rc; log="$(mktemp)"
    if "$@" >"$log" 2>&1; then rm -f "$log"; return 0; fi
    rc=$?; cat "$log" >&2; rm -f "$log"; return "$rc"
}
trap '[ -n "${FAILED:-}" ] || { FAILED=1; printf "\n%s✗ Deployment failed in step %s/%s (%s). Steps after it did not run; the previous container keeps running unless step 5 had started.%s\n" "$RED" "$STEP" "$TOTAL" "$STEP_NAME" "$NC" >&2; }' ERR
START=$SECONDS
PREV="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CONTAINER" 2>/dev/null || true)"

##############################################################################
# Step 1: Code - check out exactly the commit to deploy
##############################################################################
step "Checking out $REF"
[ -d "$SRC/.git" ] || quiet git clone --filter=blob:none "$REPO_URL" "$SRC"
quiet git -C "$SRC" fetch --prune origin
if git -C "$SRC" rev-parse --verify --quiet "origin/$REF^{commit}" >/dev/null; then
    TARGET="origin/$REF"
elif git -C "$SRC" rev-parse --verify --quiet "$REF^{commit}" >/dev/null; then
    TARGET="$REF"
else
    die "'$REF' is not a pushed branch, tag or commit. Push it first."
fi
git -C "$SRC" -c advice.detachedHead=false checkout --quiet --force --detach "$TARGET"
SHA="$(git -C "$SRC" rev-parse HEAD)"; SHORT="${SHA:0:7}"
SUBJECT="$(git -C "$SRC" log -1 --format=%s)"
ok "Commit $SHORT: $SUBJECT"
if [ -z "$PREV" ]; then info "first deploy (nothing running yet)"
elif [ "$PREV" = "$SHA" ]; then info "same commit as the running container (redeploy)"
else info "running now: ${PREV:0:7}  ->  deploying: $SHORT"; fi

##############################################################################
# Step 2: Config - RustFS key and TLS certificate (placed once, never by this script)
#
# The container runs as uid 10001 and mounts credentials/ and tls/ read-only,
# so both must be owned by 10001. Values are never read or printed here.
##############################################################################
step "RustFS key and certificate"
for f in credentials/rustfs_access_key credentials/rustfs_secret_key; do
    [ -s "$CFG/$f" ] || die "Missing $CFG/$f (the limited RustFS key; README, Deployment)."
    [ "$(stat -c %u "$CFG/$f")" = 10001 ] || die "$CFG/$f must be owned by 10001 (the container user)."
done
ok "RustFS key present (owner 10001)"
[ -s "$CFG/tls/cert.pem" ] && [ -s "$CFG/tls/key.pem" ] || die "No certificate in $CFG/tls (certbot; README, Deployment)."
CERT_END="$(openssl x509 -in "$CFG/tls/cert.pem" -noout -enddate | cut -d= -f2)"
CERT_DAYS=$(( ($(date -d "$CERT_END" +%s) - $(date +%s)) / 86400 ))
CERT_DATE="$(date -d "$CERT_END" +%Y-%m-%d)"
if [ "$CERT_DAYS" -lt 0 ]; then die "Certificate EXPIRED on $CERT_DATE. Run: certbot renew"
elif [ "$CERT_DAYS" -lt 21 ]; then warn "Certificate expires in $CERT_DAYS days ($CERT_DATE); renewal should have happened. Check: certbot renew --dry-run"
else ok "Certificate valid until $CERT_DATE ($CERT_DAYS days left)"; fi

##############################################################################
# Step 3: certbot hook from this commit (copies renewed certificates for uid 10001)
##############################################################################
step "Installing the certbot hook"
install -m 755 "$SVC/scripts/cert-hook.sh" /usr/local/sbin/file-management-cert-hook
ok "certbot renewal hook -> /usr/local/sbin/file-management-cert-hook"

##############################################################################
# Step 4: Build the image (labelled with the commit; the running container is
# not touched until step 5)
##############################################################################
step "Building image (this can take a minute on the first deploy)"
cd "$SVC"
quiet env REVISION="$SHA" docker compose build file-management
IMAGE="$(docker compose config --images file-management)"
SIZE="$(docker image inspect -f '{{.Size}}' "$IMAGE" | numfmt --to=iec)"
ok "Built $IMAGE ($SIZE, revision $SHORT)"

##############################################################################
# Step 5: Start and wait until healthy (Docker's HEALTHCHECK calls /health,
# which also checks that both buckets are reachable with the RustFS key)
##############################################################################
step "Starting container"
quiet env REVISION="$SHA" docker compose up -d file-management
info "waiting for the health check (up to 60 s)..."
STATUS=starting
for i in $(seq 1 30); do
    STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER")"
    [ "$STATUS" = healthy ] && break
    sleep 2
done
if [ "$STATUS" != healthy ]; then
    docker compose logs --tail 40 file-management >&2
    die "Container is '$STATUS' after 60 s (logs above)."
fi
ok "Container healthy after $(( i * 2 )) s"

##############################################################################
# Step 6: Check it the way callers reach it: DNS, port 443, certificate verified
##############################################################################
step "Checking https://$NAME/health"
HEALTH="$(curl -fsS "https://$NAME/health")" || die "Not reachable over TLS by name (DNS, port 443, certificate, or storage)."
ok "Reachable by name, certificate verified: $HEALTH"
RUNNING="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CONTAINER")"
[ "$RUNNING" = "$SHA" ] || die "Container reports revision ${RUNNING:0:7}, expected $SHORT."

##############################################################################
# Step 7: Smoke test
#
# scripts/smoke_test.py runs in the new image with host networking: a token
# from the live auth-service as `smoke-test` (only grant: private/smoke-test/),
# then PUT/GET/Range/LIST/DELETE on real RustFS, and four requests outside its
# folder that must be refused. Real clients are never used.
##############################################################################
step "Smoke test (as the smoke-test client)"
[ -s "$SMOKE_SECRET" ] || die "Missing $SMOKE_SECRET (copy it from the auth host; README, Deployment)."
if SMOKE_OUT="$(docker run --rm --network host --read-only --user 0 --cap-drop ALL \
        --security-opt no-new-privileges:true \
        -v "$SMOKE_SECRET:/t/secret:ro" -v "$SVC/scripts/smoke_test.py:/t/smoke.py:ro" \
        --entrypoint python "$IMAGE" /t/smoke.py 2>&1)"; then
    printf '%s\n' "$SMOKE_OUT" | grep -E '^(PASS|FAIL)' | sed 's/^/  /'
    ok "Smoke test passed ($(printf '%s\n' "$SMOKE_OUT" | grep -c '^PASS') checks)"
else
    printf '%s\n' "$SMOKE_OUT" | sed 's/^/  /' >&2
    die "Smoke test FAILED. The new version is running; roll back with: infra/deployment/file-management-service.sh ${PREV:0:7}"
fi

quiet docker image prune -f --filter "label=org.opencontainers.image.title=file-management"

##############################################################################
# Summary
##############################################################################
printf '\n%s========================================%s\n' "$GREEN" "$NC"
printf '%sfile-management deployed in %d s%s\n' "$GREEN" "$(( SECONDS - START ))" "$NC"
printf '%s========================================%s\n' "$GREEN" "$NC"
printf 'URL:          %s%s%s\n' "$BLUE" "https://$NAME" "$NC"
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf 'Commit:       %s%s%s %s  (was %s)\n' "$BLUE" "$SHORT" "$NC" "$SUBJECT" "${PREV:0:7}"
else
    printf 'Commit:       %s%s%s %s\n' "$BLUE" "$SHORT" "$NC" "$SUBJECT"
fi
printf 'Certificate:  valid until %s (%s days)\n' "$CERT_DATE" "$CERT_DAYS"
printf '\nUseful commands (on the host: ssh files-host):\n'
printf '  Logs:         docker compose -f %s/compose.yaml logs -f file-management\n' "$SVC"
printf '  Audit:        docker logs %s 2>&1 | grep file_management.audit\n' "$CONTAINER"
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf '  Roll back:    infra/deployment/file-management-service.sh %s   (from the Mac)\n' "${PREV:0:7}"
else
    printf '  Roll back:    infra/deployment/file-management-service.sh <older commit sha>   (from the Mac)\n'
fi
}
deploy "$@" </dev/null
REMOTE
