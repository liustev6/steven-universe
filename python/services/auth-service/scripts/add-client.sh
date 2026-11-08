#!/usr/bin/env bash
# Create (or rotate) a client's secret on the auth-service host and pipe it
# straight into a file on the service's host. Run on the Mac.
#
#   scripts/add-client.sh <client_id> <service_host> <secret_path> <owner> [--rotate]
#   scripts/add-client.sh pi-server panel /etc/pi-server/credentials/client_secret liustev6
#
# The secret only passes through the pipe: it is never shown, stored on this Mac,
# or written to shell history. The target file is replaced only if a secret
# actually arrived, so a failure can't leave the service with an empty file.
# AUTH_HOST picks the auth-service SSH host (default: auth-host).
# Spec: docs/specs/auth-service.md, "Onboarding a new service".
set -euo pipefail

if [[ $# -lt 4 ]]; then
  sed -n '2,8p' "$0" >&2
  exit 2
fi

CLIENT_ID="$1"
SERVICE_HOST="$2"
SECRET_PATH="$3"
OWNER="$4"
COMMAND="add-client"
[[ "${5:-}" == "--rotate" ]] && COMMAND="rotate-secret"
AUTH_HOST="${AUTH_HOST:-auth-host}"

# Quote for the remote shell. sudo only when the remote user isn't root (LXC hosts log in as root
# and have no sudo; the Pi logs in as a normal user).
printf -v REMOTE_WRITE 'S=; [ "$(id -u)" -eq 0 ] || S=sudo; $S sh -c %q' "
  umask 077
  tmp=\$(mktemp)
  cat > \"\$tmp\"
  if [ ! -s \"\$tmp\" ]; then rm -f \"\$tmp\"; echo 'no secret received' >&2; exit 1; fi
  install -D -m 600 -o $(printf %q "$OWNER") \"\$tmp\" $(printf %q "$SECRET_PATH")
  rm -f \"\$tmp\"
"

ssh -o BatchMode=yes "$AUTH_HOST" "auth-service-admin $COMMAND $(printf %q "$CLIENT_ID") --stdout" \
  | ssh -o BatchMode=yes "$SERVICE_HOST" "$REMOTE_WRITE"

echo "==> $COMMAND $CLIENT_ID: secret written to $SERVICE_HOST:$SECRET_PATH"
if [[ "$COMMAND" == "rotate-secret" ]]; then
  echo "    restart the service, check its first mint (auth-service-admin list-clients), then:"
  echo "    ssh $AUTH_HOST auth-service-admin promote-secret $CLIENT_ID"
else
  echo "    set SERVICE_AUTH_CLIENT_ID=$CLIENT_ID and SERVICE_AUTH_CLIENT_SECRET_FILE (or LoadCredential), then restart it"
fi
