#!/usr/bin/env bash
# Build the home panel app for the Pi and copy it over (run on the Mac).
#
#   scripts/run_deploy.sh              build + copy app + install launcher
#   scripts/run_deploy.sh --run        ...then switch the Pi screen to the app
#   scripts/run_deploy.sh --skip-build copy the existing build only
#
# PI_HOST picks the SSH host (default: the `panel` alias in ~/.ssh/config).
# On the Pi: app in ~/panel/app, start script + icon in ~/panel, launcher on
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
  'sudo systemctl stop home-panel-app 2>/dev/null || true; mkdir -p ~/panel/app'

step "Copying app to $PI_HOST:~/panel/app"
rsync -a --delete "$BUILD_DIR/" "$PI_HOST:panel/app/"

step "Installing start script, icon and launcher"
rsync -a deploy/start-panel.sh deploy/home-panel.svg deploy/home-panel.desktop \
  "$PI_HOST:panel/"
ssh -o BatchMode=yes "$PI_HOST" '
  set -e
  chmod +x ~/panel/start-panel.sh
  mkdir -p ~/Desktop ~/.local/share/applications
  for target in ~/Desktop ~/.local/share/applications; do
    sed "s#@HOME@#$HOME#g" ~/panel/home-panel.desktop > "$target/home-panel.desktop"
    chmod +x "$target/home-panel.desktop"
  done
  rm ~/panel/home-panel.desktop
'

if $run; then
  step "Switching the $PI_HOST screen to the app"
  ssh -o BatchMode=yes "$PI_HOST" '~/panel/start-panel.sh'
fi

step "Done"
if ! $run; then
  echo "Tap 'Home Panel' on the Pi desktop, or re-run with --run."
fi
echo "Back to the desktop: ssh $PI_HOST 'sudo systemctl stop home-panel-app; sudo systemctl start lightdm'"
