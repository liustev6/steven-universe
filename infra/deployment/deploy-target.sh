#!/usr/bin/env bash

##############################################################################
# deploy-target: the host-side half of every deploy (one script for all services).
#
# Started by the wrapper /usr/local/sbin/deploy-target (deploy-target-wrapper.sh; called by deploy.sh
# over SSH, later by the deployer) after it has checked out the commit being deployed (so this
# file, the manifest and the service code are all from that commit):
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
#   4. Pull the image        8. Smoke test
#
# The image comes from GHCR (.github/workflows/build.yml), named by the commit that last changed
# the service's code or its libs (the "image sha", deployer spec D1a); hosts never build.
# Every deploy appends one line to /var/lib/deploy-target/<service>.log:
#   <time> TAB <deployed sha> TAB <image sha> TAB <digest> TAB succeeded|failed
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
out("M_LIBS", " ".join("python/libs/" + lib for lib in m.get("libs") or []))
out("M_FILES", " ".join(m.get("files") or []))
out("M_IMAGES", " ".join(m.get("images") or []))
PY
)"
[ "$M_SERVICE" = "$SERVICE" ] || { echo "manifest says service '$M_SERVICE', expected '$SERVICE'" >&2; exit 1; }
[ -n "$M_CODE" ] || { echo "manifest has no 'code' folder (needed for the image sha)" >&2; exit 1; }

TOTAL=$([ -n "$M_PRE_START" ] && echo 8 || echo 7)
# shellcheck source=lib.sh
source "$SRC/infra/deployment/lib.sh"
START=$SECONDS
SHORT="${SHA:0:7}"; SUBJECT="$(git -C "$SRC" log -1 --format=%s)"
SVC_DIR="$SRC/$(dirname "$M_COMPOSE")"
cd "$SVC_DIR"

# The image to run (D1a). Exported: every `docker compose` below, and the service hook, use it.
# shellcheck disable=SC2086  # $M_LIBS: repository paths, no spaces
IMAGE_SHA="$(git -C "$SRC" log -1 --format=%H "$SHA" -- "$M_CODE" $M_LIBS)"
export IMAGE_TAG="$IMAGE_SHA"
DIGEST=""

# Deploy log (D10): the previous successful deploy, and this one's result when the script ends.
LOG="/var/lib/deploy-target/$SERVICE.log"
install -d -m 700 "$(dirname "$LOG")"
PREV="$(awk -F'\t' '$5 == "succeeded" { s = $2 } END { print s }' "$LOG" 2>/dev/null || true)"
if [ -z "$PREV" ]; then   # deploys from before the log: the running container's revision label
    CID="$(docker compose ps -q "$SERVICE" 2>/dev/null || true)"
    PREV="$([ -n "$CID" ] && docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CID" 2>/dev/null || true)"
fi
record() { printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "$SHA" "$IMAGE_SHA" "${DIGEST:--}" "$1" >>"$LOG"; }
trap 'rc=$?; [ "$rc" -eq 0 ] || record failed' EXIT

##############################################################################
# Step 1: the commit (already checked out by deploy.sh's SSH command)
##############################################################################
step "Commit"
ok "$SHORT: $SUBJECT"
if [ -z "$PREV" ]; then info "first deploy (no successful deploy recorded)"
elif [ "$PREV" = "$SHA" ]; then info "same commit as the last successful deploy (redeploy)"
else info "last successful: ${PREV:0:7}  ->  deploying: $SHORT"; fi
[ -z "$PREV" ] || info "previous: $PREV"        # full sha: the deployer rolls back to it (spec D8)
if [ "$IMAGE_SHA" = "$SHA" ]; then info "image: built from this commit"
else info "image: ${IMAGE_SHA:0:7} (last change to $M_CODE or its libs; this commit changes only config)"; fi

##############################################################################
# Step 2: config.env, secrets from OpenBao, host-only files, certificate
##############################################################################
step "Config and secrets"
install -d -m 700 -o 10001 -g 10001 "$CFG"
if [ -n "$M_CONFIG" ]; then
    install -m 600 -o 10001 -g 10001 "$SRC/$M_CONFIG" "$CFG/config.env"
    ok "config.env from $M_CONFIG"
fi

# Other config files (manifest `files`): installed as $CFG/<name>, with each ${image:<name>} replaced by
# that image (manifest `images`, infra/images/<name>.yaml) pinned by digest. Each image is named like
# a service's (the last commit touching its code and libs) and pulled here, before anything restarts.
if [ -n "$M_FILES" ]; then
    REGISTRY="$(dirname "$(docker compose config --images "$SERVICE")")"     # ghcr.io/<owner>/<repo>
    FILL_ARGS=()
    for img in $M_IMAGES; do
        eval "$(python3 - "$SRC/infra/images/$img.yaml" <<'PY'
import shlex, sys, yaml
f = yaml.safe_load(open(sys.argv[1]))
print("I_CODE=" + shlex.quote(f["code"]))
print("I_LIBS=" + shlex.quote(" ".join("python/libs/" + lib for lib in f.get("libs") or [])))
PY
)"
        # shellcheck disable=SC2086  # $I_LIBS: repository paths, no spaces
        I_SHA="$(git -C "$SRC" log -1 --format=%H "$SHA" -- "$I_CODE" $I_LIBS)"
        I_REF="$REGISTRY/$img:$I_SHA"
        docker image inspect "$I_REF" >/dev/null 2>&1 || quiet docker pull "$I_REF" \
            || die "Cannot pull $I_REF. Is its build finished? (gh run list --workflow build.yml)"
        I_LABEL="$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$I_REF")"
        [ "$I_LABEL" = "$I_SHA" ] || die "$I_REF says revision ${I_LABEL:0:7}, expected ${I_SHA:0:7}."
        I_PINNED="$(docker image inspect -f '{{index .RepoDigests 0}}' "$I_REF")"
        FILL_ARGS+=("$img=$I_PINNED")
        I_DIGEST="${I_PINNED#*@}"
        ok "image $img: ${I_SHA:0:7} (${I_DIGEST:0:19}…)"
    done
    INSTALLED=()
    for f in $M_FILES; do
        dest="$CFG/$(basename "$f")"
        python3 "$SRC/infra/deployment/fill-images.py" "$SRC/$f" "$dest.new" "${FILL_ARGS[@]}" \
            || die "Cannot install $f (see above)."
        chown 10001:10001 "$dest.new"; chmod 600 "$dest.new"; mv "$dest.new" "$dest"
        INSTALLED+=("$dest")
        ok "$(basename "$f") from $f"
    done
    # The files reach the container through a mount, which compose can't see change. Their hash goes
    # into the container's configuration (compose.yaml labels it with CONFIG_FILES_SHA), so a change
    # recreates it, as Helm's checksum/config annotation does. plan.sh checks the compose uses it.
    CONFIG_FILES_SHA="$(cat "${INSTALLED[@]}" | sha256sum | cut -c1-16)"
    export CONFIG_FILES_SHA
    info "config files hash: $CONFIG_FILES_SHA (a change recreates the container)"
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
# Step 4: pull the image from GHCR (the running container is untouched)
##############################################################################
step "Pulling image"
IMAGE="$(docker compose config --images "$SERVICE")"     # ghcr.io/.../<service>:<image sha>
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    info "already on this host"
else
    quiet docker pull "$IMAGE" \
        || die "Cannot pull $IMAGE. Is its build finished? (gh run list --workflow build.yml; to build an older commit: gh workflow run build.yml -f ref=$SHA)"
fi
LABEL="$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE")"
[ "$LABEL" = "$IMAGE_SHA" ] || die "$IMAGE says revision ${LABEL:0:7}, expected ${IMAGE_SHA:0:7}."
DIGEST="$(docker image inspect -f '{{index .RepoDigests 0}}' "$IMAGE")"; DIGEST="${DIGEST#*@}"
# The deployer passes the digest GitHub built (through the wrapper): the image must be exactly that.
[ -z "${EXPECTED_DIGEST:-}" ] || [ "$DIGEST" = "$EXPECTED_DIGEST" ] \
    || die "$IMAGE is $DIGEST, but the request expects $EXPECTED_DIGEST."
ok "$(basename "${IMAGE%:*}"):${IMAGE_SHA:0:7} ($(docker image inspect -f '{{.Size}}' "$IMAGE" | numfmt --to=iec), ${DIGEST:0:19}…)"

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
quiet docker compose up -d --no-build --pull never "$SERVICE"
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
[ "$RUNNING" = "$IMAGE_SHA" ] || die "Container reports revision ${RUNNING:0:7}, expected ${IMAGE_SHA:0:7}."

##############################################################################
# Smoke test: the service's script, in the new image, with host networking
##############################################################################
step "Smoke test"
if [ -n "$M_SMOKE_SCRIPT" ]; then
    SMOKE_MOUNTS=(-v "$SRC/$M_SMOKE_SCRIPT:/t/smoke.py:ro")
    if [ -n "$M_SMOKE_SECRET" ]; then           # optional: a smoke test may need no credentials
        [ -s "$M_SMOKE_SECRET" ] || die "Missing $M_SMOKE_SECRET (it should have been delivered from OpenBao in step 2)."
        SMOKE_MOUNTS+=(-v "$M_SMOKE_SECRET:/t/secret:ro")
    fi
    if SMOKE_OUT="$(docker run --rm --network host --read-only --user 0 --cap-drop ALL \
            --security-opt no-new-privileges:true "${SMOKE_MOUNTS[@]}" \
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

# Old images of this service (a rollback pulls again: seconds).
quiet docker image prune -af --filter "label=org.opencontainers.image.title=$SERVICE"
record succeeded

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
printf 'Image:        %s@%s\n' "${IMAGE%:*}" "$DIGEST"
printf 'Secrets:      %s from OpenBao (%s changed)\n' "$COUNT" "$CHANGED"
printf 'Certificate:  valid until %s (%s days)\n' "$CERT_DATE" "$CERT_DAYS"
printf '\nLogs:         docker compose -f %s logs -f %s   (on the host)\n' "$SRC/$M_COMPOSE" "$SERVICE"
if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ]; then
    printf 'Roll back:    infra/deployment/deploy.sh %s %s   (from the Mac)\n' "$SERVICE" "${PREV:0:7}"
else
    printf 'Roll back:    infra/deployment/deploy.sh %s <older commit sha>   (from the Mac)\n' "$SERVICE"
fi
