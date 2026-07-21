#!/bin/sh
set -e
# Named volumes are often root-owned; fix ownership then drop privileges.
if [ -d /app/data ]; then
  chown -R bot:bot /app/data || true
fi
if [ "$(id -u)" = "0" ]; then
  exec runuser -u bot -- "$@"
fi
exec "$@"
