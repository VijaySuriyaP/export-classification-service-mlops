FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements/llm-stub.txt requirements/
RUN pip install -r requirements/llm-stub.txt

COPY app/__init__.py app/__init__.py
COPY app/llm_stub/ app/llm_stub/
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.llm_stub.main:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
