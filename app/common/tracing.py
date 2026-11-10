"""OpenTelemetry setup and cross-service context propagation (D5).

The API injects the W3C trace context into the queued job; the worker extracts it, so a run is a
single trace from ``POST /runs`` through every agent step, tool call and model call.
"""

from __future__ import annotations

import logging

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log = logging.getLogger(__name__)
_configured = False


def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    """Install a tracer provider. With no endpoint, spans are created (so IDs exist) but not exported."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint.rstrip("/") + "/v1/traces"))
        )
    trace.set_tracer_provider(provider)
    _configured = True
    log.info("tracing configured", extra={"otlp_export": bool(otlp_endpoint)})


def tracer(name: str = "ecs") -> trace.Tracer:
    return trace.get_tracer(name)


def inject_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def extract_context(carrier: dict[str, str] | None) -> Context:
    return propagate.extract(carrier or {})


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
