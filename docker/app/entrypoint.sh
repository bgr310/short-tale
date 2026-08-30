#!/usr/bin/env bash
# Fix ownership of bind-mounted dirs (the host decides their uid) then drop
# to the unprivileged user.
set -euo pipefail

for d in /app/data /app/out /app/models; do
  mkdir -p "$d"
  chown -R shorttale:shorttale "$d" 2>/dev/null || true
done

if [ "$(id -u)" = "0" ]; then
  exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
fi
exec "$@"
