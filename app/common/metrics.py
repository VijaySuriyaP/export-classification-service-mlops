"""Prometheus metric definitions (F8, A6, D5, L5).

All metric names are prefixed ``ecs_``. The API and the worker each expose the metrics they update;
process CPU/memory/start-time metrics come from ``prometheus_client`` automatically and give
per-container resource utilisation and uptime.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_RUN_BUCKETS = (0.5, 1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300)
_STEP_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60)
_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)

# ---------------- API ----------------
HTTP_REQUESTS = Counter("ecs_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("ecs_http_request_duration_seconds", "HTTP latency", ["method", "route"], buckets=_HTTP_BUCKETS)
RUNS_SUBMITTED = Counter("ecs_runs_submitted_total", "Runs accepted and enqueued")
REQUESTS_REJECTED = Counter("ecs_requests_rejected_total", "Requests rejected at ingestion and quarantined", ["reason"])
RATE_LIMITED = Counter("ecs_rate_limited_total", "Requests refused by the per-client rate limiter")
QUEUE_DEPTH = Gauge("ecs_queue_depth", "Runs waiting in the queue")

# ---------------- Worker: runs ----------------
RUNS_FINISHED = Counter("ecs_runs_finished_total", "Runs that reached a terminal state", ["status", "route"])
RUN_END_TO_END = Histogram(
    "ecs_run_end_to_end_seconds", "Submission to final result (the SLO metric)", buckets=_RUN_BUCKETS
)
RUN_EXECUTION = Histogram("ecs_run_execution_seconds", "Worker execution time of a run", buckets=_RUN_BUCKETS)
RUN_QUEUE_WAIT = Histogram("ecs_run_queue_wait_seconds", "Time between submission and start", buckets=_RUN_BUCKETS)
RUN_STEPS = Histogram("ecs_run_steps", "Agent steps per run", buckets=(1, 2, 3, 4, 6, 8, 10, 12, 16, 20))
RUN_TOOL_CALLS = Histogram("ecs_run_tool_calls", "Tool calls per run", buckets=(0, 1, 2, 3, 4, 6, 8, 10, 15, 20))
RUN_CACHE = Counter("ecs_run_cache_total", "Whole-run cache lookups", ["result"])
POLICY_ADJUSTMENTS = Counter("ecs_output_policy_adjustments_total", "Output policy interventions", ["rule"])
WORKER_BUSY = Gauge("ecs_worker_busy", "1 while the worker is executing a run")
RUNS_REQUEUED = Counter("ecs_runs_requeued_total", "Runs requeued after a worker died mid-run")

# ---------------- Worker: agents & tools ----------------
AGENT_STEPS = Counter("ecs_agent_steps_total", "Agent steps by outcome", ["agent", "step", "outcome"])
AGENT_STEP_LATENCY = Histogram(
    "ecs_agent_step_duration_seconds", "Agent step latency", ["agent", "step"], buckets=_STEP_BUCKETS
)
TOOL_CALLS = Counter("ecs_tool_calls_total", "Tool calls by outcome", ["agent", "tool", "outcome"])
TOOL_LATENCY = Histogram("ecs_tool_call_duration_seconds", "Tool call latency", ["tool"], buckets=_STEP_BUCKETS)

# ---------------- Worker: model provider ----------------
LLM_CALLS = Counter("ecs_llm_calls_total", "Model calls by outcome", ["provider", "outcome"])
LLM_LATENCY = Histogram("ecs_llm_call_duration_seconds", "Model call latency", ["provider"], buckets=_STEP_BUCKETS)
LLM_RETRIES = Counter("ecs_llm_retries_total", "Model call retries", ["provider"])
LLM_TOKENS = Counter("ecs_llm_tokens_total", "Tokens", ["provider", "agent", "kind"])
LLM_COST = Counter("ecs_llm_cost_usd_total", "Model cost in USD at time of call", ["provider", "agent"])
LLM_FALLBACKS = Counter("ecs_llm_fallback_total", "Calls served by the fallback provider")
CIRCUIT_STATE = Gauge("ecs_circuit_breaker_state", "0=closed 1=half_open 2=open", ["provider"])
CIRCUIT_REJECTIONS = Counter("ecs_circuit_breaker_rejections_total", "Calls refused by an open breaker", ["provider"])
