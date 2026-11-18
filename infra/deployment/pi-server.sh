#!/usr/bin/env bash
# Deploy pi-server to the Pi and (re)start it as a systemd service (run on
# the Mac).
#
#   infra/deployment/pi-server.sh   (from the repo root, or any directory)
#
# PI_HOST picks the SSH host (default: the `pi` alias in ~/.ssh/config).
# On the Pi: code in ~/pi-server, Python venv in ~/pi-server/.venv, settings
# in ~/.local/share/pi-server, service `pi-server` (starts at boot, restarts
# on failure, listens on 127.0.0.1:8765 only).
set -euo pipefail

PI_HOST="${PI_HOST:-pi}"
SERVICE_DIR="$(cd "$(dirname "$0")/../../python/services/pi-server" && pwd)"

step() { printf '\n==> %s\n' "$*"; }

cd "$SERVICE_DIR"

step "Copying pi-server to $PI_HOST:~/pi-server"
ssh -o BatchMode=yes "$PI_HOST" 'mkdir -p ~/pi-server/libs'
rsync -a --delete --exclude '__pycache__' app/ "$PI_HOST:pi-server/app/"
rsync -a requirements.txt deploy/pi-server.service "$PI_HOST:pi-server/"
rsync -a deploy/pi-server.env "$PI_HOST:pi-server/.env"
rsync -a --delete --exclude '__pycache__' --exclude '*.egg-info' --exclude tests \
  ../../libs/service-auth/ "$PI_HOST:pi-server/libs/service-auth/"     # its token client (the voice turn)

step "Installing Python packages and the service"
ssh -o BatchMode=yes "$PI_HOST" '
  set -e
  cd ~/pi-server
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  .venv/bin/pip install --quiet ./libs/service-auth
  sed -e "s#@USER@#$(id -un)#g" -e "s#@HOME@#$HOME#g" pi-server.service \
    | sudo tee /etc/systemd/system/pi-server.service > /dev/null
  rm pi-server.service
  sudo systemctl daemon-reload
  sudo systemctl enable --quiet pi-server
  sudo systemctl restart pi-server
'

step "Checking health"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ssh -o BatchMode=yes "$PI_HOST" 'curl -fs localhost:8765/health'; then
    echo
    step "Done"
    exit 0
  fi
  sleep 1
done
echo "pi-server didn't answer; see: ssh $PI_HOST journalctl -u pi-server -n 50" >&2
exit 1
