#!/usr/bin/env bash
set -euo pipefail

echo "[publisher] starting virtual display ${DISPLAY} (${SCREEN_GEOMETRY})"
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_GEOMETRY}" -nolisten tcp &
sleep 1

# A minimal WM so Chromium's windows behave and dialogs are movable.
fluxbox >/dev/null 2>&1 &

# VNC is bound inside the container only; compose maps it to 127.0.0.1.
# No password is set because the port is never reachable off this host —
# if you change that port binding, add -rfbauth and a password file.
x11vnc -display "${DISPLAY}" -forever -shared -nopw -quiet -rfbport 5900 &
websockify --web=/usr/share/novnc 7900 localhost:5900 >/dev/null 2>&1 &

echo "[publisher] noVNC ready on :7900  ->  http://localhost:7900/vnc.html"
exec python3 -m uvicorn publisher.service:app --host 0.0.0.0 --port 8090
