#!/bin/sh
# Switch the screen from the Pi desktop to Hey Pi.
#
# flutter-pi needs the display to itself, so the desktop (lightdm) has to stop
# first. Both steps run inside a transient systemd unit, which keeps going
# after the desktop, and anything launched from it, closes.
#
# Back to the desktop: sudo systemctl stop hey-pi; sudo systemctl start lightdm
set -eu

APP_DIR="$HOME/hey-pi/app"
USER_NAME="$(id -un)"

sudo systemctl stop hey-pi 2>/dev/null || true

sudo systemd-run --unit=hey-pi --collect \
  sh -c "systemctl stop lightdm; sleep 1; exec runuser -u $USER_NAME -- \
$APP_DIR/flutter-pi --release --dimensions 269,158 $APP_DIR"
