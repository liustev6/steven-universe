#!/usr/bin/env bash
# Build Hey Pi for the Pi and copy it over (run on the Mac).
#
#   scripts/run_deploy.sh              build + copy app + install launcher
#   scripts/run_deploy.sh --run        ...then switch the Pi screen to the app
#   scripts/run_deploy.sh --skip-build copy the existing build only
#
# PI_HOST picks the SSH host (default: the `panel` alias in ~/.ssh/config).
# On the Pi: app in ~/hey-pi/app, start script + icon in ~/hey-pi, launcher on
# the desktop and in the app menu. Nothing is set to start at boot.
set -euo pipefail

PI_HOST="${PI_HOST:-panel}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$APP_DIR/build/flutter-pi/pi5-64"

run=false
build=true
for arg in "$@"; do
  case "$arg" in
    --run) run=true ;;
    --skip-build) build=false ;;
    -h | --help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg (see --help)" >&2
      exit 2
      ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

cd "$APP_DIR"

if $build; then
  step "Building release bundle for Pi 5"
  flutterpi_tool build --arch=arm64 --cpu=pi5 --release
fi

if [[ ! -x "$BUILD_DIR/flutter-pi" ]]; then
  echo "No build found at $BUILD_DIR (run without --skip-build)." >&2
  exit 1
fi

step "Stopping the app on $PI_HOST (if running)"
ssh -o BatchMode=yes "$PI_HOST" \
  'sudo systemctl stop hey-pi 2>/dev/null || true; mkdir -p ~/hey-pi/app'

step "Copying app to $PI_HOST:~/hey-pi/app"
rsync -a --delete "$BUILD_DIR/" "$PI_HOST:hey-pi/app/"

step "Installing start script, icon and launcher"
rsync -a deploy/start-hey-pi.sh deploy/hey-pi.svg deploy/hey-pi.desktop \
  "$PI_HOST:hey-pi/"
ssh -o BatchMode=yes "$PI_HOST" '
  set -e
  chmod +x ~/hey-pi/start-hey-pi.sh
  mkdir -p ~/Desktop ~/.local/share/applications
  for target in ~/Desktop ~/.local/share/applications; do
    sed "s#@HOME@#$HOME#g" ~/hey-pi/hey-pi.desktop > "$target/hey-pi.desktop"
    chmod +x "$target/hey-pi.desktop"
  done
  rm ~/hey-pi/hey-pi.desktop
'

if $run; then
  step "Switching the $PI_HOST screen to the app"
  ssh -o BatchMode=yes "$PI_HOST" '~/hey-pi/start-hey-pi.sh'
fi

step "Done"
if ! $run; then
  echo "Tap 'Hey Pi' on the Pi desktop, or re-run with --run."
fi
echo "Back to the desktop: ssh $PI_HOST 'sudo systemctl stop hey-pi; sudo systemctl start lightdm'"
