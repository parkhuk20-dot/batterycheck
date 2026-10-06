# Single container for hosts that expose one port (Render): Next.js on $PORT, FastAPI on loopback.
FROM node:22-bookworm-slim AS web
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend ./
COPY shared /app/shared
RUN npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home batterycheck \
    && mkdir /data && chown batterycheck:batterycheck /data
COPY backend/batterycheck /app/batterycheck
COPY shared /shared
COPY --from=web --chown=batterycheck:batterycheck /app/frontend /app/frontend
COPY deploy/render-start.sh /app/start.sh
USER batterycheck
ENV BATTERYCHECK_DB=/data/batterycheck.sqlite3
CMD ["sh", "/app/start.sh"]
