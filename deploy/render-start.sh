#!/bin/sh
set -e
# Render provides RENDER_EXTERNAL_URL (https://<name>.onrender.com); a custom domain needs BATTERYCHECK_ALLOWED_ORIGINS.
export BATTERYCHECK_ALLOWED_ORIGINS="${BATTERYCHECK_ALLOWED_ORIGINS:-$RENDER_EXTERNAL_URL}"
export BATTERYCHECK_COOKIE_SECURE="${BATTERYCHECK_COOKIE_SECURE:-1}"
# ponytail: no supervisor; if the API process dies the site answers 503 until the next deploy/restart.
cd /app && python -m uvicorn batterycheck.main:app --host 127.0.0.1 --port 8814 --workers 1 --no-access-log &
cd /app/frontend && exec node node_modules/next/dist/bin/next start --hostname 0.0.0.0 --port "${PORT:-10000}"
