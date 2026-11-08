#!/usr/bin/env bash

##############################################################################
# deploy.sh: deploy any service described in infra/services/<service>/deploy.yaml (from the Mac).
#
#   infra/deployment/deploy.sh <service>          deploy main (latest pushed commit)
#   infra/deployment/deploy.sh <service> <ref>    a pushed branch, tag or sha (also how to roll back)
#
# What it does:
#   1. resolves <ref> to a pushed commit and reads that commit's manifest (never the working copy)
#   2. fetches the secrets the manifest declares from OpenBao, with your login (BAO_TOKEN)
#   3. opens ONE SSH session to the manifest's host: checks out the commit there, then runs
#      infra/deployment/deploy-target.sh (from that commit), passing the secrets on stdin.
#      The secrets are never written on the Mac; on the host they go straight to their files.
#
# Before deploying a service that declares secrets, log in to OpenBao (valid 1 hour):
#   export BAO_ADDR=https://bao.lan.liustev6.ca:8200
#   export BAO_TOKEN="$(bao login -method=userpass -token-only username=steven)"
#
# Requires on the Mac: git, yq, bao. Spec: docs/specs/deployer.md (D12), secrets-manager.md (S15-S17).
##############################################################################

set -euo pipefail

SERVICE="${1:-}"
REF="${2:-main}"
REPO_URL="https://github.com/liustev6/steven-universe.git"
export BAO_ADDR="${BAO_ADDR:-https://bao.lan.liustev6.ca:8200}"

if [ -t 1 ]; then COLOR=1; RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
else COLOR=0; RED=''; GREEN=''; BLUE=''; NC=''; fi
die() { echo -e "${RED}✗ $*${NC}" >&2; exit 1; }

[[ "$SERVICE" =~ ^[a-z][a-z0-9-]{1,62}$ ]] || die "usage: $0 <service> [ref]   (services: $(ls "$(dirname "$0")/../services" | tr '\n' ' '))"
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid ref: $REF"
for tool in git yq bao ssh base64; do command -v "$tool" >/dev/null || die "missing tool: $tool"; done

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
NSECRETS="$(yq -r '.secrets // [] | length' <<<"$MANIFEST")"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deploying ${SERVICE}${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Commit:   ${BLUE}${SHA:0:7}${NC} ($REF) $(git -C "$REPO" log -1 --format=%s "$SHA")"
echo -e "Host:     ${BLUE}$HOST${NC}"
echo -e "Secrets:  ${BLUE}$NSECRETS${NC} from OpenBao ($BAO_ADDR)"

# ---- 2. Secrets from OpenBao (in memory only) ------------------------------------------------------
# One line per secret: <destination> TAB <owner> TAB <base64 of the value, formatted>.
PAYLOAD=""
if [ "$NSECRETS" -gt 0 ]; then
    bao token lookup >/dev/null 2>&1 \
        || die "Not logged in to OpenBao. Run: export BAO_TOKEN=\"\$(bao login -method=userpass -token-only username=steven)\""
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

# ---- 3. One SSH session: check out the commit on the host, run deploy-target.sh from it -------------
REMOTE="set -e
SRC=/opt/steven-universe
[ -d \"\$SRC/.git\" ] || git clone --quiet --filter=blob:none $REPO_URL \"\$SRC\"
git -C \"\$SRC\" fetch --quiet --prune origin
git -C \"\$SRC\" -c advice.detachedHead=false checkout --quiet --force --detach $SHA
exec bash \"\$SRC/infra/deployment/deploy-target.sh\" $SERVICE $SHA $COLOR"

printf '%s' "$PAYLOAD" | ssh -o BatchMode=yes "$HOST" "$REMOTE"
