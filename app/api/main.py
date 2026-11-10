"""FastAPI application: ingestion boundary, run submission and status polling (F1, F2, D1, A4, F8).

Why polling and not callbacks (A4): the web client runs in a browser and cannot receive inbound
callbacks; polling needs no public endpoint on the caller's side and is trivially load-testable.

Run with:  uvicorn --factory app.api.main:create_app
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

import redis
from fastapi import FastAPI, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from app import __version__
from app.common import metrics
from app.common.config import Settings, get_settings
from app.common.logging_setup import configure_logging, correlation_id_var, run_id_var
from app.common.run_store import QuarantineStore, RateLimiter, RunStore, new_id, utcnow
from app.common.tracing import configure_tracing, current_trace_id, inject_context
from app.contracts import (
    FieldError,
    RunRecord,
    RunRequest,
    RunStatusResponse,
    SubmitResponse,
    ValidationErrorResponse,
)
from app.guardrails.input_screen import screen_fields

log = logging.getLogger("api")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_RUN_ID = r"^[0-9a-f]{32}$"

DESCRIPTION = """
Decision-support service that proposes an HS classification and export-policy finding for a
product description. **It never files a declaration; a licensed customs broker decides.**

Flow: `POST /runs` validates the request at the ingestion boundary (rejects are quarantined with
field-level errors and no run is created), enqueues a run and returns `202` with a `run_id`.
Poll `GET /runs/{run_id}` until `status` is `completed`, `escalated` or `failed`.
`GET /runs/{run_id}/events` reconstructs the run: every step, tool call, passage and model call.

Headers: `X-Correlation-ID` (optional, echoed and propagated), `X-Client-ID` (rate-limit key;
defaults to the caller's IP).
"""


def _field_errors(exc: ValidationError) -> list[FieldError]:
    out: list[FieldError] = []
    for err in exc.errors(include_url=False):
        fields = (err.get("ctx") or {}).get("fields")
        if fields:
            name = "+".join(fields)
        elif err["loc"]:
            name = ".".join(str(p) for p in err["loc"])
        else:
            name = "body"
        out.append(FieldError(field=name, message=err["msg"], type=err["type"]))
    return out


def create_app(settings: Settings | None = None, redis_client: redis.Redis | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("api", settings.log_level)
    configure_tracing("api", settings.otel_exporter_otlp_endpoint)
    if redis_client is None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is not set (see .env.example)")
        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                            socket_timeout=5, socket_connect_timeout=5)

    store = RunStore(redis_client, settings.run_ttl_seconds)
    quarantine = QuarantineStore(redis_client)
    limiter = RateLimiter(redis_client, settings.rate_limit_per_minute)

    app = FastAPI(title="Export Classification Check Service", version=__version__, description=DESCRIPTION)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "X-Correlation-ID", "X-Client-ID"],
                           expose_headers=["X-Correlation-ID", "Location", "Retry-After"])

    @app.middleware("http")
    async def correlation_and_metrics(request: Request, call_next):
        supplied = request.headers.get("x-correlation-id", "")
        cid = supplied if _SAFE_ID.match(supplied) else uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            route = request.scope.get("route")
            label = getattr(route, "path", "unmatched")
            metrics.HTTP_REQUESTS.labels(request.method, label, str(status)).inc()
            metrics.HTTP_LATENCY.labels(request.method, label).observe(time.perf_counter() - started)
            if label not in {"/health", "/metrics"}:
                log.info("http request", extra={"method": request.method, "route": label, "status": status,
                                                "latency_ms": round((time.perf_counter() - started) * 1000, 1)})
            correlation_id_var.reset(token)

    def client_id_of(request: Request) -> str:
        supplied = request.headers.get("x-client-id", "")
        if _CLIENT_ID.match(supplied):
            return supplied
        return request.client.host if request.client else "unknown"

    def reject(cid: str, client_id: str, reason: str, errors: list[FieldError], raw: bytes) -> JSONResponse:
        qid = quarantine.add({
            "correlation_id": cid, "client_id": client_id, "reason": reason,
            "errors": [e.model_dump() for e in errors], "raw_body": raw[:4096].decode("utf-8", "replace"),
        })
        metrics.REQUESTS_REJECTED.labels(reason).inc()
        log.info("request quarantined", extra={"reason": reason, "quarantine_id": qid,
                                               "fields": [e.field for e in errors]})
        body = ValidationErrorResponse(quarantine_id=qid, correlation_id=cid, errors=errors)
        return JSONResponse(status_code=422, content=body.model_dump())

    # ------------------------------------------------------------------ routes
    @app.get("/health", tags=["ops"], summary="Liveness")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"], summary="Readiness: dependencies reachable")
    def ready() -> JSONResponse:
        try:
            redis_client.ping()
            return JSONResponse({"status": "ready", "redis": "ok", "queue_depth": store.queue_depth()})
        except redis.RedisError as exc:
            return JSONResponse(status_code=503, content={"status": "not_ready", "redis": str(exc)})

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def prometheus_metrics() -> Response:
        try:
            metrics.QUEUE_DEPTH.set(store.queue_depth())
        except redis.RedisError:
            pass
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/contract", tags=["contract"], summary="Published data contract (JSON Schema)")
    def contract() -> dict:
        from app.contracts import ClassificationResult

        return {
            "version": "1",
            "request": RunRequest.model_json_schema(),
            "result": ClassificationResult.model_json_schema(),
            "run_status": RunStatusResponse.model_json_schema(),
            "validation_error": ValidationErrorResponse.model_json_schema(),
        }

    @app.post(
        "/runs",
        status_code=202,
        tags=["runs"],
        summary="Submit a product for classification",
        response_model=SubmitResponse,
        responses={
            422: {"model": ValidationErrorResponse, "description": "Rejected and quarantined; no run created"},
            429: {"description": "Per-client rate limit exceeded"},
        },
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": RunRequest.model_json_schema()}}}},
    )
    async def submit_run(request: Request):
        cid = correlation_id_var.get() or uuid.uuid4().hex
        client_id = client_id_of(request)

        if settings.rate_limit_enabled:
            allowed, retry_after = await run_in_threadpool(limiter.check, client_id)
            if not allowed:
                metrics.RATE_LIMITED.inc()
                return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)},
                                    content={"error": "rate_limited", "retry_after_s": retry_after,
                                             "limit_per_minute": settings.rate_limit_per_minute})

        raw = await request.body()
        if len(raw) > settings.max_request_bytes:
            return await run_in_threadpool(reject, cid, client_id, "too_large", [FieldError(
                field="body", message=f"body exceeds {settings.max_request_bytes} bytes", type="too_large")], raw)
        if "application/json" not in request.headers.get("content-type", ""):
            return await run_in_threadpool(reject, cid, client_id, "content_type", [FieldError(
                field="body", message="Content-Type must be application/json", type="content_type")], raw)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return await run_in_threadpool(reject, cid, client_id, "malformed_json", [FieldError(
                field="body", message=f"invalid JSON: {exc}", type="json_invalid")], raw)
        try:
            run_request = RunRequest.model_validate(payload)
        except ValidationError as exc:
            return await run_in_threadpool(reject, cid, client_id, "schema", _field_errors(exc), raw)

        hits = screen_fields(run_request.model_text_fields())
        if hits:
            errors = [FieldError(field=f, message="input rejected by prompt-injection guardrail: " + ", ".join(p),
                                 type="prompt_injection_suspected") for f, p in hits.items()]
            return await run_in_threadpool(reject, cid, client_id, "prompt_injection", errors, raw)

        run_id = new_id()
        now = utcnow()
        record = RunRecord(run_id=run_id, status="queued", created_at=now, updated_at=now, correlation_id=cid,
                           trace_id=current_trace_id(), client_id=client_id,
                           request=run_request.model_dump(mode="json"))
        job = {"run_id": run_id, "correlation_id": cid, "trace_context": inject_context(),
               "enqueued_at": now.isoformat()}
        await run_in_threadpool(store.submit, record, job)
        metrics.RUNS_SUBMITTED.inc()
        token = run_id_var.set(run_id)
        log.info("run submitted", extra={"client_id": client_id})
        run_id_var.reset(token)
        body = SubmitResponse(run_id=run_id, status="queued", status_url=f"/runs/{run_id}", correlation_id=cid)
        return JSONResponse(status_code=202, content=body.model_dump(), headers={"Location": body.status_url})

    @app.get("/runs/{run_id}", tags=["runs"], summary="Poll a run", response_model=RunStatusResponse,
             responses={404: {"description": "Unknown or expired run"}})
    def get_run(run_id: str = Path(pattern=_RUN_ID)):
        record = store.get(run_id)
        if record is None:
            return JSONResponse(status_code=404, content={"error": "not_found", "run_id": run_id})
        return RunStatusResponse.from_record(record)

    @app.get("/runs/{run_id}/events", tags=["runs"], summary="Reconstruct a run from its event log")
    def get_run_events(run_id: str = Path(pattern=_RUN_ID)):
        record = store.get(run_id)
        if record is None:
            return JSONResponse(status_code=404, content={"error": "not_found", "run_id": run_id})
        return {"run": RunStatusResponse.from_record(record).model_dump(mode="json"),
                "events": store.events(run_id)}

    @app.get("/quarantine", tags=["ops"], summary="Most recent rejected requests and their field errors")
    def get_quarantine(limit: int = Query(default=20, ge=1, le=200)) -> dict:
        return {"total": quarantine.count(), "items": quarantine.recent(limit)}

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    except ImportError:  # pragma: no cover
        pass
    return app
