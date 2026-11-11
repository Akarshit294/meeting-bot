#!/usr/bin/env bash
# Runs scripts/browser_check.py inside the bot image (usage: see that file). Starts the screen and the sound
# the way bot/entrypoint.sh does in service mode (Xvfb + PulseAudio), then the check instead of the bot.
set -euo pipefail
Xvfb "$DISPLAY" -screen 0 "${SCREEN_SIZE}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
for _ in $(seq 50); do [ -S "/tmp/.X11-unix/X${DISPLAY#:}" ] && break; sleep 0.1; done
mkdir -p /tmp/pulse
export XDG_RUNTIME_DIR=/tmp/pulse PULSE_SERVER=unix:/tmp/pulse/native
pulseaudio -n --daemonize=yes --exit-idle-time=-1 --disallow-exit --use-pid-file=no \
  -F /app/pulse.pa --log-target=file:/tmp/pulse.log
for _ in $(seq 50); do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
exec python "$(dirname "$0")/browser_check.py"
