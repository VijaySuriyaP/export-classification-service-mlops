FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface

WORKDIR /srv

COPY requirements/base.txt requirements/worker.txt requirements/
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install -r requirements/worker.txt

COPY app/ app/
COPY corpus/index/ corpus/index/

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

RUN useradd --create-home --uid 10001 app && chown -R app:app /opt/huggingface
USER app

EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9100/metrics', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "app.worker.worker"]
