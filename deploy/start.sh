#!/bin/sh
set -e
# Vercel provides its hostnames without a scheme; a custom domain needs BATTERYCHECK_ALLOWED_ORIGINS set explicitly.
if [ -z "$BATTERYCHECK_ALLOWED_ORIGINS" ]; then
  for host in $VERCEL_PROJECT_PRODUCTION_URL $VERCEL_URL $VERCEL_BRANCH_URL; do
    BATTERYCHECK_ALLOWED_ORIGINS="${BATTERYCHECK_ALLOWED_ORIGINS:+$BATTERYCHECK_ALLOWED_ORIGINS,}https://$host"
  done
  export BATTERYCHECK_ALLOWED_ORIGINS
fi
export BATTERYCHECK_COOKIE_SECURE="${BATTERYCHECK_COOKIE_SECURE:-1}"
# ponytail: no supervisor; if the API process dies this instance answers 503 until it is recycled.
cd /app && python -m uvicorn batterycheck.main:app --host 127.0.0.1 --port 8814 --workers 1 --no-access-log &
# A cold instance must not accept web traffic before the API answers.
until python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8814/health', timeout=1)" 2>/dev/null; do sleep 0.2; done
cd /app/frontend && exec node node_modules/next/dist/bin/next start --hostname 0.0.0.0 --port "${PORT:-80}"
