FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    # The database lives on a persistent disk mounted at /data.
    JOBHUB_DATABASE_URL=sqlite:////data/jobhub.db \
    # One process serves the site and runs the 2-hour collection.
    JOBHUB_SCHEDULER_ENABLED=1

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ .
RUN mkdir -p /data && useradd --create-home app && chown -R app /app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')" || exit 1

# Single worker on purpose: the scheduler runs inside this process.
CMD ["sh", "-c", "uvicorn jobhub.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
