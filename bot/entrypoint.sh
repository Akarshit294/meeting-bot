#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "record" ]; then
  shift
  exec python /app/human_mouse.py record "$@"
fi

join_only=0
for arg in "$@"; do [ "$arg" = "--join-only" ] && join_only=1; done
[ "${JOIN_ONLY:-0}" = "1" ] && join_only=1

Xvfb "$DISPLAY" -screen 0 "${SCREEN_SIZE}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
for _ in $(seq 50); do
  [ -S "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
  sleep 0.1
done

if [ "${ENABLE_VNC:-1}" = "1" ]; then
  x11vnc -display "$DISPLAY" -localhost -nopw -forever -shared -quiet -nocursorshape \
    -bg -o /tmp/x11vnc.log >/dev/null
  websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/novnc.log 2>&1 &
  for _ in $(seq 50); do
    (exec 3<>/dev/tcp/127.0.0.1/6080) 2>/dev/null && break
    sleep 0.1
  done
  echo "Live view (if port 6080 is published): http://localhost:6080 (watch only; /control.html to click in it)"
fi

# Sound (docs/SPEC.md 5.1): virtual devices only. Not in --join-only, which keeps today's fake devices.
if [ "$join_only" = "0" ]; then
  mkdir -p /tmp/pulse
  export XDG_RUNTIME_DIR=/tmp/pulse PULSE_SERVER=unix:/tmp/pulse/native
  pulseaudio -n --daemonize=yes --exit-idle-time=-1 --disallow-exit --use-pid-file=no \
    -F /app/pulse.pa --log-target=file:/tmp/pulse.log
  for _ in $(seq 50); do
    pactl info >/dev/null 2>&1 && break
    sleep 0.1
  done
  pactl list short sinks | cut -f2 | tr '\n' ' ' | sed 's/^/Sound: sinks /'; echo
fi

if [ "${1:-}" = "mouse-demo" ]; then
  shift
  exec python /app/human_mouse.py demo "$@"
fi
exec python /app/meet_bot.py "$@"
