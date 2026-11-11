"""Run lifecycle contracts: submission, status polling, errors."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.contracts.results import ClassificationResult

RunStatus = Literal["queued", "running", "completed", "escalated", "failed"]
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "escalated", "failed"})


class ErrorDetail(BaseModel):
    code: str
    message: str


class RunUsage(BaseModel):
    """Per-run totals recorded by the worker (A6, L5)."""

    steps: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    execution_seconds: float | None = None
    end_to_end_seconds: float | None = None


class RunRecord(BaseModel):
    """Internal persisted state of a run (stored in Redis)."""

    run_id: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    correlation_id: str
    trace_id: str | None = None
    client_id: str
    request: dict[str, Any]
    attempts: int = 0
    worker_id: str | None = None
    cache_hit: bool = False
    result: ClassificationResult | None = None
    error: ErrorDetail | None = None
    usage: RunUsage = Field(default_factory=RunUsage)


class SubmitResponse(BaseModel):
    run_id: str
    status: RunStatus
    status_url: str
    correlation_id: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    correlation_id: str
    trace_id: str | None = None
    attempts: int
    cache_hit: bool
    client_reference: str | None = None
    result: ClassificationResult | None = None
    error: ErrorDetail | None = None
    usage: RunUsage
    events_url: str

    @classmethod
    def from_record(cls, record: RunRecord) -> "RunStatusResponse":
        return cls(
            run_id=record.run_id,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            correlation_id=record.correlation_id,
            trace_id=record.trace_id,
            attempts=record.attempts,
            cache_hit=record.cache_hit,
            client_reference=record.request.get("client_reference"),
            result=record.result,
            error=record.error,
            usage=record.usage,
            events_url=f"/runs/{record.run_id}/events",
        )


class FieldError(BaseModel):
    field: str
    message: str
    type: str


class ValidationErrorResponse(BaseModel):
    error: Literal["validation_failed"] = "validation_failed"
    quarantine_id: str
    correlation_id: str
    errors: list[FieldError]
    run_created: Literal[False] = False
