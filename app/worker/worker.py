"""Agent worker: consumes runs from the queue and executes them under the platform's guarantees.

Guarantees per run:
  * bounded by steps / tool calls / model calls / wall-clock time (A1);
  * provider failure -> circuit breaker -> fallback provider -> escalation, never a guess (A5);
  * output policy applied before storing (A3, L4);
  * a run that did not complete is never returned as a confident answer;
  * a worker crash mid-run leaves the job in its processing list, to be requeued (A2).
"""

from __future__ import annotations

import contextvars
import importlib
import json
import logging
import signal
import socket
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import redis
from prometheus_client import start_http_server

from app.common import metrics
from app.common.config import Settings, get_settings
from app.common.logging_setup import configure_logging, correlation_id_var, run_id_var
from app.common.run_store import RunStore, utcnow
from app.common.tracing import configure_tracing, extract_context, tracer
from app.contracts import TERMINAL_STATUSES, ClassificationResult, ErrorDetail, RunRecord, RunRequest, Verification
from app.guardrails.output_policy import enforce_output_policy, run_status_for
from app.llm import LLMClient, ProviderUnavailableError
from app.worker.recorder import LoopBudgetExceeded, RunLimits, RunRecorder

log = logging.getLogger("worker")


def load_runner(path: str, **kwargs: Any):
    module_name, _, class_name = path.partition(":")
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls(**kwargs)


class Worker:
    def __init__(self, settings: Settings, store: RunStore, runner, llm: LLMClient | None, worker_id: str) -> None:
        self.settings = settings
        self.store = store
        self.runner = runner
        self.llm = llm
        self.worker_id = worker_id
        self.prompt_version = getattr(runner, "prompt_version", "unknown")
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")
        self._stop = False
        self.poll_timeout_s = 2.0
        self._sleep = time.sleep
        self._needs_requeue = False
        self._redis_backoff = 0.0
        self._last_reap = 0.0

    # ------------------------------------------------------------------ loop
    def stop(self, *_: Any) -> None:
        log.info("stop requested; finishing current run")
        self._stop = True

    def run_forever(self) -> None:
        self._needs_requeue = True  # recover anything a previous instance left in our processing list
        while not self._stop:
            self.loop_iteration()

    def loop_iteration(self) -> None:
        """One pass of the worker loop.

        A Redis outage never kills the worker: it backs off exponentially and keeps retrying. A run
        that was in flight when Redis went away stays in this worker's processing list and is
        requeued once Redis is back. Re-running it is safe: a run already in a terminal state is
        skipped, and an unfinished one is retried up to ``RUN_MAX_ATTEMPTS``.
        """
        try:
            if self._needs_requeue:
                self._requeue_own_jobs()
                self._needs_requeue = False
            self._housekeeping()
            raw = self.store.dequeue(self.worker_id, timeout_s=self.poll_timeout_s)
        except redis.RedisError as exc:
            self._on_redis_error(exc)
            return
        self._redis_backoff = 0.0
        if raw is None:
            return
        try:
            self.process(json.loads(raw))
        except redis.RedisError as exc:
            self._on_redis_error(exc)  # job stays in the processing list until Redis recovers
            return
        except Exception:  # never let one bad job kill the worker
            log.exception("unhandled error processing job")
        try:
            self.store.ack(self.worker_id, raw)
        except redis.RedisError as exc:
            self._on_redis_error(exc)

    def _requeue_own_jobs(self) -> None:
        recovered = self.store.requeue_processing(self.worker_id)
        if recovered:
            metrics.RUNS_REQUEUED.inc(recovered)
            log.warning("requeued unfinished runs from this worker", extra={"count": recovered})

    def _housekeeping(self) -> None:
        self.store.heartbeat(self.worker_id, self.settings.worker_heartbeat_ttl_s)
        if time.monotonic() - self._last_reap > self.settings.worker_heartbeat_ttl_s:
            reaped = self.store.reap_dead_workers()
            if reaped:
                metrics.RUNS_REQUEUED.inc(reaped)
                log.warning("requeued runs from dead workers", extra={"count": reaped})
            self._last_reap = time.monotonic()
        metrics.QUEUE_DEPTH.set(self.store.queue_depth())

    def _on_redis_error(self, exc: Exception) -> None:
        metrics.REDIS_ERRORS.labels("worker").inc()
        self._needs_requeue = True
        self._redis_backoff = min(10.0, max(0.5, self._redis_backoff * 2))
        log.error("redis unavailable; backing off", extra={"error": type(exc).__name__,
                                                          "retry_in_s": self._redis_backoff})
        self._sleep(self._redis_backoff)

    # ------------------------------------------------------------------ one run
    def process(self, job: dict[str, Any]) -> None:
        run_id = job["run_id"]
        tokens = (correlation_id_var.set(job.get("correlation_id")), run_id_var.set(run_id))
        try:
            with tracer().start_as_current_span(
                "run.execute", context=extract_context(job.get("trace_context")),
                attributes={"run.id": run_id, "worker.id": self.worker_id},
            ):
                self._process(run_id)
        finally:
            correlation_id_var.reset(tokens[0])
            run_id_var.reset(tokens[1])

    def _process(self, run_id: str) -> None:
        record = self.store.get(run_id)
        if record is None:
            log.warning("job for unknown or expired run")
            return
        if record.status in TERMINAL_STATUSES:
            return
        record.attempts += 1
        if record.attempts > self.settings.run_max_attempts:
            self._finish(record, "failed", error=ErrorDetail(
                code="max_attempts_exceeded", message="Run was interrupted repeatedly; not retried further."))
            return

        record.status = "running"
        record.started_at = utcnow()
        record.worker_id = self.worker_id
        self.store.save(record)
        self.store.append_event(run_id, {"ts": utcnow().isoformat(), "kind": "run_started",
                                         "worker_id": self.worker_id, "attempt": record.attempts,
                                         "prompt_version": self.prompt_version})
        metrics.RUN_QUEUE_WAIT.observe((record.started_at - record.created_at).total_seconds())
        request = RunRequest.model_validate(record.request)

        # whole-run cache (D7)
        cache_key = None
        if self.settings.run_cache_ttl_seconds > 0:
            cache_key = self.store.cache_key(record.request, self.prompt_version)
            cached = self.store.cache_get(cache_key)
            if cached:
                metrics.RUN_CACHE.labels("hit").inc()
                result = ClassificationResult.model_validate_json(cached)
                record.cache_hit = True
                self.store.append_event(run_id, {"ts": utcnow().isoformat(), "kind": "cache_hit"})
                self._finish(record, run_status_for(result), result=result)
                return
            metrics.RUN_CACHE.labels("miss").inc()

        limits = RunLimits(
            max_steps=self.settings.run_max_steps,
            max_tool_calls=self.settings.run_max_tool_calls,
            max_llm_calls=self.settings.run_max_llm_calls,
            deadline=time.monotonic() + self.settings.run_wall_clock_timeout_s,
        )
        recorder = RunRecorder(self.store, run_id, limits, self.llm, self.settings.tool_timeout_s)
        started = time.perf_counter()
        metrics.WORKER_BUSY.set(1)
        try:
            result = self._execute(request, recorder)
            result = enforce_output_policy(result, recorder)
            if result.policy_adjustments:
                recorder.event("output_policy", adjustments=result.policy_adjustments)
            if cache_key:
                self.store.cache_set(cache_key, result.model_dump_json(), self.settings.run_cache_ttl_seconds)
            self._finish(record, run_status_for(result), result=result, recorder=recorder, started=started)
        except LoopBudgetExceeded as exc:
            recorder.event("budget_exceeded", reason=exc.reason)
            self._finish(record, "escalated", recorder=recorder, started=started, result=self._escalation(
                f"Agent loop budget exhausted ({exc.reason}) before a verified answer was reached."))
        except ProviderUnavailableError as exc:
            recorder.event("degraded", reason="model provider unavailable", detail=str(exc))
            self._finish(record, "escalated", recorder=recorder, started=started, result=self._escalation(
                "Model provider unavailable (retries exhausted / circuit open); no classification attempted. "
                "Resubmit later or refer to a specialist."))
        except Exception as exc:
            log.exception("run failed")
            recorder.event("run_error", error=type(exc).__name__, detail=str(exc)[:500])
            self._finish(record, "failed", recorder=recorder, started=started,
                         error=ErrorDetail(code="internal_error", message=f"{type(exc).__name__}: run did not complete"))
        finally:
            metrics.WORKER_BUSY.set(0)
            recorder.close()

    def _execute(self, request: RunRequest, recorder: RunRecorder) -> ClassificationResult:
        """Run the agent in a thread so the wall-clock bound holds even if agent code blocks."""
        ctx = contextvars.copy_context()
        future = self._executor.submit(ctx.run, self.runner.run, request, recorder)
        try:
            result = future.result(timeout=max(recorder.remaining_s(), 0.001))
        except FutureTimeout:
            # The stuck thread is abandoned; its next recorder call fails the budget check.
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")
            raise LoopBudgetExceeded("wall-clock limit reached") from None
        if not isinstance(result, ClassificationResult):
            result = ClassificationResult.model_validate(result)
        return result

    def _escalation(self, reason: str) -> ClassificationResult:
        return ClassificationResult(
            route="specialist-classification-review",
            basis=reason,
            verification=Verification(verified=False, notes="run did not complete normally"),
            escalation_reason=reason,
            prompt_version=self.prompt_version,
        )

    def _finish(self, record: RunRecord, status: str, *, result: ClassificationResult | None = None,
                error: ErrorDetail | None = None, recorder: RunRecorder | None = None,
                started: float | None = None) -> None:
        now = utcnow()
        record.status = status  # type: ignore[assignment]
        record.result = result
        record.error = error
        record.finished_at = now
        if recorder is not None:
            for key, value in recorder.usage().items():
                setattr(record.usage, key, value)
            metrics.RUN_STEPS.observe(recorder.steps)
            metrics.RUN_TOOL_CALLS.observe(recorder.tool_calls)
        if started is not None:
            record.usage.execution_seconds = round(time.perf_counter() - started, 3)
            metrics.RUN_EXECUTION.observe(record.usage.execution_seconds)
        record.usage.end_to_end_seconds = round((now - record.created_at).total_seconds(), 3)
        self.store.save(record)
        route = result.route if result else "none"
        self.store.append_event(record.run_id, {"ts": now.isoformat(), "kind": "run_finished", "status": status,
                                                "route": route, "usage": record.usage.model_dump()})
        metrics.RUNS_FINISHED.labels(status, route).inc()
        metrics.RUN_END_TO_END.observe(record.usage.end_to_end_seconds)
        log.info("run finished", extra={"status": status, "route": route,
                                        "end_to_end_s": record.usage.end_to_end_seconds})


def _connect_redis(url: str) -> redis.Redis:
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=10, socket_connect_timeout=5,
                                  health_check_interval=30)
    for attempt in range(1, 11):
        try:
            client.ping()
            return client
        except redis.ConnectionError:
            delay = min(10, 0.5 * 2 ** attempt)
            log.warning("redis not reachable; retrying", extra={"attempt": attempt, "delay_s": delay})
            time.sleep(delay)
    raise SystemExit("redis unreachable")


def main() -> None:
    settings = get_settings()
    configure_logging("agent-worker", settings.log_level)
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is not set (see .env.example)")
    configure_tracing("agent-worker", settings.otel_exporter_otlp_endpoint)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass
    start_http_server(settings.worker_metrics_port)
    store = RunStore(_connect_redis(settings.redis_url), settings.run_ttl_seconds)
    llm = LLMClient.from_settings(settings)
    runner = load_runner(settings.agent_runner, llm=llm, settings=settings)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    worker = Worker(settings, store, runner, llm, worker_id)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    log.info("worker started", extra={"worker_id": worker_id, "runner": settings.agent_runner,
                                      "prompt_version": worker.prompt_version,
                                      "providers": [p.name for p in llm.providers]})
    worker.run_forever()


if __name__ == "__main__":
    main()
