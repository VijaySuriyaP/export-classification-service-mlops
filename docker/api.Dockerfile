FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements/base.txt requirements/api.txt requirements/
RUN pip install -r requirements/api.txt

COPY app/ app/
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

# One process: prometheus_client metrics are per-process. Scale with replicas, not uvicorn workers.
CMD ["uvicorn", "--factory", "app.api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
