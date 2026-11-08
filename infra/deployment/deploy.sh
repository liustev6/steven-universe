#!/usr/bin/env bash

##############################################################################
# deploy.sh: deploy any service described in infra/services/<service>/deploy.yaml (from the Mac).
#
#   infra/deployment/deploy.sh <service>          deploy main (latest pushed commit)
#   infra/deployment/deploy.sh <service> <ref>    a pushed branch, tag or sha (also how to roll back)
#
# What it does:
#   1. resolves <ref> to a pushed commit and reads that commit's manifest (never the working copy);
#      checks that the service's image is in GHCR (built by .github/workflows/build.yml), waiting
#      for a build that is still running
#   2. fetches the secrets the manifest declares from OpenBao, with your login (BAO_TOKEN)
#   3. opens ONE SSH session to the manifest's host and runs its wrapper /usr/local/sbin/deploy-target,
#      which checks out the commit and runs that commit's deploy-target.sh, secrets on stdin.
#      The secrets are never written on the Mac; on the host they go straight to their files.
#
# OpenBao login: if there is no valid one, the script asks for your password (user $BAO_USER,
# default steven) and the bao CLI keeps the login for 1 hour, so the next deploys don't ask again.
# An exported BAO_TOKEN is used if it is still valid. OpenBao must be unsealed (docs/specs/boot.md).
#
# Requires on the Mac: git, yq, bao, curl; gh (optional: waits for a running build). Spec: docs/specs/deployer.md (D12), secrets-manager.md (S15-S17).
##############################################################################

set -euo pipefail

SERVICE="${1:-}"
REF="${2:-main}"
GH_REPO="liustev6/steven-universe"
export BAO_ADDR="${BAO_ADDR:-https://bao.lan.liustev6.ca:8200}"
BAO_USER="${BAO_USER:-steven}"

if [ -t 1 ]; then COLOR=1; RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
else COLOR=0; RED=''; GREEN=''; BLUE=''; NC=''; fi
die() { echo -e "${RED}✗ $*${NC}" >&2; exit 1; }

[[ "$SERVICE" =~ ^[a-z][a-z0-9-]{1,62}$ ]] || die "usage: $0 <service> [ref]   (services: $(ls "$(dirname "$0")/../services" | tr '\n' ' '))"
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid ref: $REF"
for tool in git yq bao ssh base64 curl; do command -v "$tool" >/dev/null || die "missing tool: $tool"; done

# ---- 1. The commit and its manifest ---------------------------------------------------------------
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
git -C "$REPO" fetch --quiet origin
if SHA="$(git -C "$REPO" rev-parse --verify --quiet "origin/$REF^{commit}")"; then :
elif SHA="$(git -C "$REPO" rev-parse --verify --quiet "$REF^{commit}")" \
     && [ -n "$(git -C "$REPO" branch -r --contains "$SHA" 2>/dev/null)" ]; then :
else die "'$REF' is not a pushed branch, tag or commit. Push it first."; fi

MANIFEST="$(git -C "$REPO" show "$SHA:infra/services/$SERVICE/deploy.yaml" 2>/dev/null)" \
    || die "no infra/services/$SERVICE/deploy.yaml at ${SHA:0:7}"
HOST="$(yq -r '.host' <<<"$MANIFEST")"
CODE="$(yq -r '.code // ""' <<<"$MANIFEST")"
[ -n "$CODE" ] || die "infra/services/$SERVICE/deploy.yaml has no 'code' folder"
# The image: named by the commit that last changed the code or python/libs/ (deployer spec D1a).
IMAGE_SHA="$(git -C "$REPO" log -1 --format=%H "$SHA" -- "$CODE" python/libs)"
IMAGE="ghcr.io/$GH_REPO/$SERVICE"
NSECRETS="$(yq -r '.secrets // [] | length' <<<"$MANIFEST")"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deploying ${SERVICE}${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Commit:   ${BLUE}${SHA:0:7}${NC} ($REF) $(git -C "$REPO" log -1 --format=%s "$SHA")"
echo -e "Host:     ${BLUE}$HOST${NC}"
echo -e "Image:    ${BLUE}$IMAGE:${IMAGE_SHA:0:7}${NC}"
echo -e "Secrets:  ${BLUE}$NSECRETS${NC} from OpenBao ($BAO_ADDR)"

# ---- The image must exist in GHCR (anonymous pull, as the host will do) --------------------------
image_exists() {
    local token
    token="$(curl -fsS "https://ghcr.io/token?scope=repository:$GH_REPO/$SERVICE:pull" | yq -r '.token')" || return 1
    curl -fsS -o /dev/null -I -H "Authorization: Bearer $token" \
        -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json" \
        "https://ghcr.io/v2/$GH_REPO/$SERVICE/manifests/$IMAGE_SHA" 2>/dev/null
}
if ! image_exists; then
    RUN_ID=""
    if command -v gh >/dev/null; then
        RUN_ID="$(gh run list -R "$GH_REPO" --workflow build.yml --limit 10 --json databaseId,status \
            --jq '[.[] | select(.status != "completed")][0].databaseId // empty' 2>/dev/null || true)"
    fi
    if [ -n "$RUN_ID" ]; then
        echo "Waiting for build run $RUN_ID (follow it: gh run watch -R $GH_REPO $RUN_ID) ..."
        gh run watch -R "$GH_REPO" "$RUN_ID" --exit-status >/dev/null 2>&1 \
            || die "Build run $RUN_ID failed: gh run view -R $GH_REPO $RUN_ID --log-failed"
    fi
    image_exists || die "No image $IMAGE:${IMAGE_SHA:0:7} in GHCR (or the package isn't public).
  Build it:  gh workflow run build.yml -R $GH_REPO -f ref=$SHA"
fi

# ---- 2. Secrets from OpenBao (in memory only) ------------------------------------------------------
# One line per secret: <destination> TAB <owner> TAB <base64 of the value, formatted>.
PAYLOAD=""
if [ "$NSECRETS" -gt 0 ]; then
    rc=0; bao status >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 2 ] || die "OpenBao is sealed: unseal it first (docs/specs/boot.md, step 1)."
    [ "$rc" -ne 1 ] || die "OpenBao at $BAO_ADDR is unreachable (Tailscale on? CT 104 up?)."
    if ! bao token lookup >/dev/null 2>&1; then
        unset BAO_TOKEN                     # an expired export would hide the CLI's stored login
        if ! bao token lookup >/dev/null 2>&1; then
            [ -t 0 ] || die "Not logged in to OpenBao. Run: BAO_ADDR=$BAO_ADDR bao login -method=userpass username=$BAO_USER"
            echo -e "OpenBao login as ${BLUE}$BAO_USER${NC} at $BAO_ADDR (kept for 1 hour by the bao CLI):"
            bao login -method=userpass -no-print username="$BAO_USER" || die "OpenBao login failed."
        fi
    fi
    for i in $(seq 0 $((NSECRETS - 1))); do
        from="$(yq -r ".secrets[$i].from" <<<"$MANIFEST")"
        to="$(yq -r ".secrets[$i].to" <<<"$MANIFEST")"
        owner="$(yq -r ".secrets[$i].owner // \"0:0\"" <<<"$MANIFEST")"
        # $(...) strips trailing newlines, which a format may need (e.g. an INI line ending in \n):
        # append a sentinel, drop it, then drop the one newline yq adds after its output.
        format="$(yq -r ".secrets[$i].format // \"{value}\"" <<<"$MANIFEST"; printf x)"
        format="${format%x}"; format="${format%$'\n'}"
        value="$(bao kv get -field=value "kv/$from" 2>/dev/null)" \
            || die "Cannot read kv/$from from OpenBao (missing, or not allowed for your login)."
        formatted="${format%%\{value\}*}${value}${format#*\{value\}}"
        PAYLOAD+="${to}"$'\t'"${owner}"$'\t'"$(printf '%s' "$formatted" | base64 | tr -d '\n')"$'\n'
    done
    unset value formatted
fi

# ---- 3. One SSH session: the host's wrapper checks out the commit and runs deploy-target.sh ---------
# /usr/local/sbin/deploy-target is installed by hand on each host (deploy-target-wrapper.sh).
REMOTE="/usr/local/sbin/deploy-target deploy $([ "$COLOR" = 1 ] && echo --color) $SERVICE $SHA"

printf '%s' "$PAYLOAD" | ssh -o BatchMode=yes "$HOST" "$REMOTE"
