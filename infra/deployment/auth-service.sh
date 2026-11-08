#!/usr/bin/env bash

##############################################################################
# auth-service Deployment Script (manual, from the Mac)
#
# Deploys auth-service to its LXC (CT 102, 192.168.50.235) with Docker Compose.
# This script runs on the Mac, but the work happens on the auth host: it opens
# ONE SSH session and sends the block between <<'REMOTE' and REMOTE to bash there.
#
# The host builds from a clean checkout of a PUSHED commit (never this Mac's
# working copy), so what runs is always a real commit. Push first, then deploy.
#
# Steps on the host:
#   1. Check out the commit           6. Signing key (first deploy only)
#   2. Config dir + TLS certificate   7. Start container, wait until healthy
#   3. Install host tools             8. Check /health by name over TLS
#   4. Build the image                9. Smoke test as the `smoke-test` client
#   5. Validate + install policy.yaml
#
# Prerequisites (one-time, see python/services/auth-service/README.md):
# - `auth-host` in ~/.ssh/config (or set AUTH_HOST)
# - Docker + Compose on the host; the TLS certificate from certbot (README step 5)
#
# Not done here: client secrets (python/services/auth-service/scripts/add-client.sh).
#
# Usage:
#   infra/deployment/auth-service.sh           # deploy main (latest pushed commit)
#   infra/deployment/auth-service.sh <ref>     # deploy a pushed branch, tag or sha
#                                              # (also how to roll back: an older sha)
##############################################################################

set -euo pipefail  # stop on errors, unset variables, and failures inside pipes

AUTH_HOST="${AUTH_HOST:-auth-host}"
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
echo -e "${GREEN}auth-service Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Target host: ${BLUE}$AUTH_HOST${NC}"
echo -e "Deploying:   ${BLUE}$REF${NC} (from GitHub)"

##############################################################################
# Everything below runs on the auth host.
#
# `bash -s -- "$REF" "$REPO_URL" "$COLOR"` reads the script from stdin and gets
# the three values as $1 $2 $3. The quoted 'REMOTE' means nothing is expanded
# on the Mac; every $VAR is evaluated on the host.
#
# The remote part is one function called with stdin from /dev/null: bash reads
# the whole definition before running it, so a command that reads stdin
# (docker compose run, used by auth-service-admin) can't swallow the script.
##############################################################################

ssh -o BatchMode=yes "$AUTH_HOST" bash -s -- "$REF" "$REPO_URL" "$COLOR" <<'REMOTE'
set -Eeuo pipefail   # -E: the ERR trap below also fires inside functions
deploy() {
REF="$1"; REPO_URL="$2"; COLOR="$3"
SRC=/opt/steven-universe                      # git checkout of the whole repo
SVC="$SRC/python/services/auth-service"       # Dockerfile, compose.yaml, policy.yaml, bin/, scripts/
CFG=/etc/auth-service                         # live config: policy, keys, clients, tls
ISSUER_HOST=auth.lan.liustev6.ca              # DNS name -> 192.168.50.235; the certificate's name
CONTAINER=auth-service-auth-service-1         # compose project "auth-service", service "auth-service"

# ---- output helpers ---------------------------------------------------------
if [ "$COLOR" = 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi
STEP=0; TOTAL=9; STEP_NAME=""
step() { STEP=$((STEP + 1)); STEP_NAME="$*"; printf '\n%sStep %d/%d: %s%s\n' "$YELLOW" "$STEP" "$TOTAL" "$*" "$NC"; }
ok()   { printf '%s✓ %s%s\n' "$GREEN" "$*" "$NC"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '%s! %s%s\n' "$YELLOW" "$*" "$NC"; }
die()  { printf '\n%s✗ %s%s\n' "$RED" "$*" "$NC" >&2; exit 1; }
# Run a noisy command silently; show its output only if it fails.
quiet() {
    local log rc; log="$(mktemp)"
    if "$@" >"$log" 2>&1; then rm -f "$log"; return 0; fi
    rc=$?; cat "$log" >&2; rm -f "$log"; return "$rc"
}
# Any unexpected failure: say which step it was (once), then stop.
trap '[ -n "${FAILED:-}" ] || { FAILED=1; printf "\n%s✗ Deployment failed in step %s/%s (%s). Steps after it did not run; the previous container keeps running unless step 7 had started.%s\n" "$RED" "$STEP" "$TOTAL" "$STEP_NAME" "$NC" >&2; }' ERR
START=$SECONDS

# What is running right now (empty on the first deploy), for the "old -> new" line.
PREV="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CONTAINER" 2>/dev/null || true)"

##############################################################################
# Step 1: Code - check out exactly the commit to deploy
##############################################################################
step "Checking out $REF"
# First run only: clone. --filter=blob:none fetches file contents lazily (smaller, faster).
[ -d "$SRC/.git" ] || quiet git clone --filter=blob:none "$REPO_URL" "$SRC"
quiet git -C "$SRC" fetch --prune origin
# A branch name means its latest pushed commit (origin/<branch>); a tag or sha is used as is.
if git -C "$SRC" rev-parse --verify --quiet "origin/$REF^{commit}" >/dev/null; then
    TARGET="origin/$REF"
elif git -C "$SRC" rev-parse --verify --quiet "$REF^{commit}" >/dev/null; then
    TARGET="$REF"
else
    die "'$REF' is not a pushed branch, tag or commit. Push it first."
fi
# Detached checkout of that exact commit; --force discards any accidental edits on the host.
git -C "$SRC" -c advice.detachedHead=false checkout --quiet --force --detach "$TARGET"
SHA="$(git -C "$SRC" rev-parse HEAD)"; SHORT="${SHA:0:7}"
SUBJECT="$(git -C "$SRC" log -1 --format=%s)"
ok "Commit $SHORT: $SUBJECT"
if [ -z "$PREV" ]; then
    info "first deploy (nothing running yet)"
elif [ "$PREV" = "$SHA" ]; then
    info "same commit as the running container (redeploy)"
else
    info "running now: ${PREV:0:7}  ->  deploying: $SHORT"
fi

##############################################################################
# Step 2: Config directory and TLS certificate
##############################################################################
step "Config directory and certificate"
# The container runs as uid 10001 and mounts this directory, so it must own it.
# Mode 700: no other user on the host can read the signing keys or client hashes.
install -d -m 700 -o 10001 -g 10001 "$CFG" "$CFG/keys" "$CFG/clients" "$CFG/tls"
ok "$CFG ready (owner 10001, mode 700)"
# The service only speaks HTTPS. certbot + its hook put the certificate here (README step 5).
if [ ! -s "$CFG/tls/cert.pem" ] || [ ! -s "$CFG/tls/key.pem" ]; then
    die "No TLS certificate in $CFG/tls. Get it first: auth-service README, step 5."
fi
CERT_END="$(openssl x509 -in "$CFG/tls/cert.pem" -noout -enddate | cut -d= -f2)"
CERT_DAYS=$(( ($(date -d "$CERT_END" +%s) - $(date +%s)) / 86400 ))
CERT_DATE="$(date -d "$CERT_END" +%Y-%m-%d)"
if [ "$CERT_DAYS" -lt 0 ]; then
    die "TLS certificate EXPIRED on $CERT_DATE. Run: certbot renew"
elif [ "$CERT_DAYS" -lt 21 ]; then
    warn "TLS certificate expires in $CERT_DAYS days ($CERT_DATE); renewal should have happened. Check: certbot renew --dry-run"
else
    ok "TLS certificate valid until $CERT_DATE ($CERT_DAYS days left)"
fi

##############################################################################
# Step 3: Host tools from this commit
##############################################################################
step "Installing host tools"
# auth-service-admin: runs the admin CLI in a one-off container (no network).
install -m 755 "$SVC/bin/auth-service-admin" /usr/local/bin/auth-service-admin
ok "auth-service-admin -> /usr/local/bin"
# certbot runs this after each renewal to copy the new certificate for the container.
if [ -f "$SVC/scripts/cert-hook.sh" ]; then
    install -m 755 "$SVC/scripts/cert-hook.sh" /usr/local/sbin/auth-service-cert-hook
    ok "certbot renewal hook -> /usr/local/sbin/auth-service-cert-hook"
else
    info "certbot hook not in this commit; keeping the installed one"
fi

##############################################################################
# Step 4: Build the image
#
# REVISION goes into the image's org.opencontainers.image.revision label, so
# `docker inspect` always shows which commit runs. The running container is
# not touched until step 7.
##############################################################################
step "Building image (this can take a minute on the first deploy)"
cd "$SVC"
quiet env REVISION="$SHA" docker compose build auth-service
IMAGE="$(docker compose config --images auth-service)"
SIZE="$(docker image inspect -f '{{.Size}}' "$IMAGE" | numfmt --to=iec)"
ok "Built $IMAGE ($SIZE, revision $SHORT)"

##############################################################################
# Step 5: Policy
#
# policy.yaml lives in git; the service reads the copy in $CFG. Copy it next
# to the live one, validate it with the new image's own checker, and only then
# swap it in. A running service notices the change and reloads it by itself.
##############################################################################
step "Policy (permissions)"
install -m 600 -o 10001 -g 10001 "$SVC/policy.yaml" "$CFG/policy.yaml.new"
if ! CHECK="$(auth-service-admin check-policy --file "$CFG/policy.yaml.new" 2>&1)"; then
    rm -f "$CFG/policy.yaml.new"
    printf '%s\n' "$CHECK" | grep -v ' Container ' >&2 || true
    die "policy.yaml at $SHORT is invalid; the live policy was not changed."
fi
SUMMARY="$(printf '%s\n' "$CHECK" | grep -o '([0-9]* audiences, [0-9]* clients)' || true)"
ok "Valid $SUMMARY"
if [ -f "$CFG/policy.yaml" ] && cmp -s "$CFG/policy.yaml" "$CFG/policy.yaml.new"; then
    rm -f "$CFG/policy.yaml.new"
    ok "Unchanged"
elif [ -f "$CFG/policy.yaml" ]; then
    info "changes (- old, + new):"
    diff -u "$CFG/policy.yaml" "$CFG/policy.yaml.new" | tail -n +3 | sed 's/^/    /' || true
    mv "$CFG/policy.yaml.new" "$CFG/policy.yaml"   # atomic: readers see old or new, never half
    ok "Updated"
else
    mv "$CFG/policy.yaml.new" "$CFG/policy.yaml"
    ok "Installed (first deploy)"
fi

##############################################################################
# Step 6: Signing key
#
# Tokens are signed with the key named in keys/active. On the first deploy
# there is none, so create one and activate it right away (no service verifies
# tokens yet). Later rotations are by hand: new-key, wait ~5 min for services
# to refresh JWKS, then promote-key.
##############################################################################
step "Signing key"
if [ -s "$CFG/keys/active" ]; then
    KID="$(cat "$CFG/keys/active")"
    ok "Active key $KID (unchanged)"
else
    KID="$(auth-service-admin new-key 2>/dev/null | awk '{print $2}')"
    quiet auth-service-admin promote-key "$KID"
    ok "Created and activated the first signing key $KID"
fi

##############################################################################
# Step 7: Start and wait until healthy
#
# `up -d` replaces the container only if the image or config changed. Docker's
# HEALTHCHECK (in the Dockerfile) calls /health inside the container.
##############################################################################
step "Starting container"
quiet env REVISION="$SHA" docker compose up -d auth-service
info "waiting for the health check (up to 60 s)..."
STATUS=starting
for i in $(seq 1 30); do
    STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER")"
    [ "$STATUS" = healthy ] && break
    sleep 2
done
if [ "$STATUS" != healthy ]; then
    docker compose logs --tail 40 auth-service >&2
    die "Container is '$STATUS' after 60 s (logs above)."
fi
ok "Container healthy after $(( i * 2 )) s"

##############################################################################
# Step 8: Check it the way clients reach it
#
# Unlike the in-container check, this goes through DNS, the published port (443,
# the one the issuer URL implies) and full certificate verification against
# auth.lan.liustev6.ca, exactly like a real client.
##############################################################################
step "Checking https://$ISSUER_HOST/health"
HEALTH="$(curl -fsS "https://$ISSUER_HOST/health")" || die "Not reachable over TLS by name (DNS, port 443, or certificate)."
field() { printf '%s' "$HEALTH" | grep -o "\"$1\":\"[^\"]*\"" | cut -d'"' -f4; }
ok "Reachable by name, certificate verified, status: $(field status)"
RUNNING="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CONTAINER")"
[ "$RUNNING" = "$SHA" ] || die "Container reports revision ${RUNNING:0:7}, expected $SHORT."

##############################################################################
# Step 9: Smoke test
#
# scripts/smoke_test.py runs inside the image just built (it has service_auth),
# with host networking, so it goes through DNS, port 443 and TLS like a real
# service: discovery, JWKS, a real mint verified against the live keys, and
# three rejections. It authenticates as `smoke-test`, a dedicated client whose
# only grant is private/smoke-test/; real clients are never touched. Its secret
# is created on the first run and kept root-only in /root/.secrets/.
##############################################################################
step "Smoke test (as the smoke-test client)"
SMOKE_SECRET=/root/.secrets/smoke-test-client
if [ ! -s "$SMOKE_SECRET" ]; then
    install -d -m 700 /root/.secrets
    if ! (umask 077; auth-service-admin add-client smoke-test --stdout >"$SMOKE_SECRET" 2>/dev/null); then
        rm -f "$SMOKE_SECRET"
        die "Could not create the smoke-test client secret (is smoke-test in policy.yaml?)."
    fi
    info "created the smoke-test client secret (first run)"
fi
# --user 0: the secret file is root-only; still read-only, no capabilities, removed afterwards.
if SMOKE_OUT="$(docker run --rm --network host --read-only --user 0 --cap-drop ALL \
        --security-opt no-new-privileges:true \
        -v "$SMOKE_SECRET:/t/secret:ro" -v "$SVC/scripts/smoke_test.py:/t/smoke.py:ro" \
        --entrypoint python "$IMAGE" /t/smoke.py 2>&1)"; then
    printf '%s\n' "$SMOKE_OUT" | grep -E '^(PASS|FAIL)' | sed 's/^/  /'
    ok "Smoke test passed ($(printf '%s\n' "$SMOKE_OUT" | grep -c '^PASS') checks)"
else
    printf '%s\n' "$SMOKE_OUT" | sed 's/^/  /' >&2
    die "Smoke test FAILED. The new version is running; roll back with: infra/deployment/auth-service.sh ${PREV:0:7}"
fi

# Remove old, now-unused auth-service images so the 8 GB disk doesn't fill up.
quiet docker image prune -f --filter "label=org.opencontainers.image.title=auth-service"

##############################################################################
# Summary
##############################################################################
printf '\n%s========================================%s\n' "$GREEN" "$NC"
printf '%sauth-service deployed in %d s%s\n' "$GREEN" "$(( SECONDS - START ))" "$NC"
printf '%s========================================%s\n' "$GREEN" "$NC"
printf 'URL:          %s%s%s\n' "$BLUE" "https://$ISSUER_HOST" "$NC"
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf 'Commit:       %s%s%s %s  (was %s)\n' "$BLUE" "$SHORT" "$NC" "$SUBJECT" "${PREV:0:7}"
else
    printf 'Commit:       %s%s%s %s\n' "$BLUE" "$SHORT" "$NC" "$SUBJECT"
fi
printf 'Signing key:  %s\n' "$(field active_kid)"
printf 'Policy:       %s %s\n' "$(field policy_version)" "$SUMMARY"
printf 'Certificate:  valid until %s (%s days)\n' "$CERT_DATE" "$CERT_DAYS"
printf '\nUseful commands (on the host: ssh auth-host):\n'
printf '  Logs:         docker compose -f %s/compose.yaml logs -f auth-service\n' "$SVC"
printf '  Clients:      auth-service-admin list-clients\n'
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf '  Roll back:    infra/deployment/auth-service.sh %s   (from the Mac)\n' "${PREV:0:7}"
else
    printf '  Roll back:    infra/deployment/auth-service.sh <older commit sha>   (from the Mac)\n'
fi
}
deploy "$@" </dev/null
REMOTE
