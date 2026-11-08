#!/usr/bin/env bash

##############################################################################
# deploy-target (wrapper): the one entry point for deploys on a host.
# Installed BY HAND as /usr/local/sbin/deploy-target, never by a deploy: it is the gatekeeper,
# so a merged commit must not be able to change it (deployer spec D6).
#
#   deploy-target deploy [--color] <service> <40-hex sha> [sha256:<digest>]
#                                                  stdin: the secrets payload (passed through)
#
# Called two ways:
#   - by the deployer, whose key is restricted in root's authorized_keys:
#       restrict,command="/usr/local/sbin/deploy-target" ssh-ed25519 AAAA... deployer
#     sshd then ignores the requested command and puts it in SSH_ORIGINAL_COMMAND (parsed here)
#   - by infra/deployment/deploy.sh from the Mac (your key, unrestricted): as arguments
#
# What it does, in order (spec D6, D9, D10, D14):
#   1. accepts exactly one form; every word is checked against a pattern, nothing is evaluated
#   2. refuses a service this host doesn't run (/etc/deploy-target/services, one name per line)
#   3. one deploy per service at a time (flock)
#   4. checks out the commit, which must be on a pushed branch
#   5. runs THAT commit's infra/deployment/deploy-target.sh (it appends the result to
#      /var/lib/deploy-target/<service>.log); with a digest, the pulled image must match it
# Every request, accepted or refused, goes to syslog (journalctl -t deploy-target).
##############################################################################

set -euo pipefail
SRC=/opt/steven-universe
REPO_URL="https://github.com/liustev6/steven-universe.git"
SERVICES_FILE=/etc/deploy-target/services
FROM="${SSH_CONNECTION%% *}"; FROM="${FROM:-local}"

refuse() {
    logger -t deploy-target "refused from $FROM: $* (request: ${REQUEST[*]:-})"
    echo "deploy-target: $*" >&2
    exit 2
}

# ---- 1. The request: one fixed form ------------------------------------------------------------
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    read -r -a REQUEST <<<"$SSH_ORIGINAL_COMMAND"    # split on spaces only; no globbing, no eval
else
    REQUEST=("$@")
fi
set -- ${REQUEST[@]+"${REQUEST[@]}"}

[ "${1:-}" = deploy ] || refuse "usage: deploy [--color] <service> <sha> [sha256:<digest>]"
shift
COLOR=0
if [ "${1:-}" = --color ]; then COLOR=1; shift; fi
[ $# -eq 2 ] || [ $# -eq 3 ] || refuse "usage: deploy [--color] <service> <sha> [sha256:<digest>]"
SERVICE="$1"; SHA="$2"; DIGEST="${3:-}"
[[ "$SERVICE" =~ ^[a-z][a-z0-9-]{1,62}$ ]] || refuse "invalid service name"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || refuse "sha must be 40 lowercase hex characters"
[ -z "$DIGEST" ] || [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || refuse "invalid digest"

# ---- 2. Only the services this host runs ----------------------------------------------------------
[ -f "$SERVICES_FILE" ] || refuse "$SERVICES_FILE is missing"
grep -qxF "$SERVICE" "$SERVICES_FILE" || refuse "$SERVICE is not deployed on this host"

# ---- 3. One deploy per service at a time ----------------------------------------------------------
exec 9>"/run/lock/deploy-target-$SERVICE.lock"
flock -n 9 || refuse "a deploy of $SERVICE is already running"

logger -t deploy-target "deploy $SERVICE ${SHA:0:12} ${DIGEST:+$DIGEST }from $FROM"

# ---- 4. The commit, from a pushed branch -----------------------------------------------------------
[ -d "$SRC/.git" ] || git clone --quiet --filter=blob:none "$REPO_URL" "$SRC"
git -C "$SRC" fetch --quiet --prune origin
git -C "$SRC" cat-file -e "$SHA^{commit}" 2>/dev/null || refuse "commit ${SHA:0:12} not found on GitHub"
[ -n "$(git -C "$SRC" branch -r --contains "$SHA")" ] || refuse "commit ${SHA:0:12} is not on a pushed branch"
git -C "$SRC" -c advice.detachedHead=false checkout --quiet --force --detach "$SHA"

# ---- 5. That commit's deploy-target.sh (stdin passed through) ---------------------------------------
export EXPECTED_DIGEST="$DIGEST"
exec bash "$SRC/infra/deployment/deploy-target.sh" "$SERVICE" "$SHA" "$COLOR"
