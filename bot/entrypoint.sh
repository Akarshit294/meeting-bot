#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "record" ]; then
  shift
  exec python /app/human_mouse.py record "$@"
fi

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

if [ "${1:-}" = "mouse-demo" ]; then
  shift
  exec python /app/human_mouse.py demo "$@"
fi
exec python /app/meet_bot.py "$@"
