FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements/base.txt requirements/worker.txt requirements/
RUN pip install -r requirements/worker.txt

COPY app/ app/
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9100/metrics', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "app.worker.worker"]
