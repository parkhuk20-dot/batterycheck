FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home batterycheck \
    && mkdir /data && chown batterycheck:batterycheck /data
COPY backend/batterycheck /app/batterycheck
COPY shared /shared
USER batterycheck
ENV BATTERYCHECK_DB=/data/batterycheck.sqlite3 BATTERYCHECK_ALLOWED_HOSTS=backend,localhost,127.0.0.1
EXPOSE 8814
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8814/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "batterycheck.main:app", "--host", "0.0.0.0", "--port", "8814", "--workers", "1", "--no-access-log"]
