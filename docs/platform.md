# Platform design — API, queue, workers, resilience, observability

Owner: NIKHIL (PES1UG24AM311). This covers requirements F1, F2, F8, A4, A5, A6, A7, D1, D3–D7.

## 1. Topology and deployment boundaries

```text
 browser / curl / Locust
        │  HTTP (X-Correlation-ID, X-Client-ID)
        ▼
 ┌──────────────┐  reject ──► Redis ecs:quarantine (field errors, raw body)
 │ api (FastAPI)│  rate limit per client (Redis fixed window)
 └──────┬───────┘
        │ RunRecord + job {run_id, correlation_id, W3C trace context}   (one MULTI/EXEC)
        ▼
 ┌──────────────┐  BLMOVE queue → ecs:queue:processing:<worker>
 │    redis     │◄────────────────────────────────────────────┐
 └──────┬───────┘                                             │
        ▼                                                     │ run state, event log, cache
 ┌──────────────────────── agent-worker × N ──────────────────┴──┐
 │ Worker ─► AgentRunner.run(request, recorder)   (wall-clock bound)│
 │           ├─ recorder.step / tool_call / llm_call  (limits, metrics, events, spans)
 │           └─ LLMClient: timeout → retry+backoff → breaker → fallback → ProviderUnavailable
 │ ─► output policy ─► completed / escalated / failed               │
 └──────────────┬──────────────────────────────────────────────────┘
                │ OpenAI-compatible HTTP
                ▼
        llm-stub (tests, demos)  or  real provider (configured in .env)

 prometheus ◄── scrapes api, every agent-worker replica (DNS SD), llm-stub, redis-exporter, jaeger, grafana
 grafana    ◄── prometheus + jaeger datasources, provisioned dashboard
 jaeger     ◄── OTLP/HTTP spans from api and workers
```

| Boundary | Why it is a separate service |
|---|---|
| `api` | Light and latency-sensitive (validation, enqueue, polling). It must stay responsive while runs take tens of seconds, and it can be released without touching agent code. It holds no run state. |
| `agent-worker` | Heavy resource profile once the agents and retrieval land (embedding model, index, many model calls). This is the unit scaled with `--scale agent-worker=N`. A crash in agent code cannot take the API down. |
| `redis` | Decouples submission from execution (A4). Holds the queue, run records, event logs, quarantine, rate-limit counters and cache. AOF persistence is on, so queued runs survive a restart. |
| `llm-stub` | Replaces the model provider in load tests (D6), so we measure our system rather than the provider's rate limits. Its failure modes are switchable for the circuit-breaker demo (A5). |
| observability (`prometheus`, `grafana`, `jaeger`, `redis-exporter`) | Kept off the request path. If any of them fails, serving still works. |

The vector index is not in this list yet. The retrieval workstream decides whether it lives in the worker container
on a volume (FAISS) or in its own service (Chroma); either fits behind `recorder.tool_call`.

## 2. Asynchronous runs (A4)

* `POST /runs` validates, then writes the run record and enqueues the job in one Redis transaction. It returns
  `202` with a `run_id` immediately.
* Callers **poll** `GET /runs/{id}`. We chose polling over callbacks because the web client is a browser page and
  cannot receive inbound calls. Polling also needs no public endpoint on the caller's side, and it is easy to
  load-test end to end.
* **Reliable queue:** a worker `BLMOVE`s a job into its own processing list and removes it only after the run
  reaches a terminal state.
  * If a worker dies mid-run, it requeues its own list on restart.
  * Any live worker also requeues the lists of workers whose heartbeat (`WORKER_HEARTBEAT_TTL_S`) has expired.
  * `RUN_MAX_ATTEMPTS` stops a poison job from looping: the run ends `failed`.

## 3. Validation and quarantine (D1, F2)

The layers run in this order. All of them run before a run exists:

1. Rate limit
2. Size limit
3. Content-Type check
4. JSON parse
5. Contract schema (schema, type, range, category, cross-field)
6. Prompt-injection screen

Each rejection is written to `ecs:quarantine` with its field errors and counted in `ecs_requests_rejected_total{reason}`.

To show that no agent run started (demo item 4):
* the response has `run_created: false`;
* `ecs_runs_submitted_total` and the queue depth are unchanged;
* `GET /quarantine` shows the record.

## 4. Resilience (A5)

| Mechanism | Where | Setting |
|---|---|---|
| Timeout per model call | `LLMClient` (httpx) | `LLM_TIMEOUT_S`, capped by the remaining run budget |
| Timeout per tool call | `RunRecorder.tool_call` (thread + deadline) | `TOOL_TIMEOUT_S` |
| Timeout per run | `Worker._execute` | `RUN_WALL_CLOCK_TIMEOUT_S` |
| Bounded retry, exponential backoff with jitter | `LLMClient.complete` on timeout, connection error, 5xx, 429 | `LLM_MAX_ATTEMPTS`, `LLM_BACKOFF_BASE_S`, `LLM_BACKOFF_MAX_S` |
| Circuit breaker per provider (closed → open → half-open with a single trial) | `app/common/resilience.py` | `CB_FAILURE_THRESHOLD`, `CB_RESET_TIMEOUT_S` |
| Loop bounds | `RunRecorder` | `RUN_MAX_STEPS`, `RUN_MAX_TOOL_CALLS`, `RUN_MAX_LLM_CALLS` |

**Degradation path for a model call:**
1. Primary provider
2. Fallback provider (if `LLM_FALLBACK_*` is set)
3. `ProviderUnavailableError` → the run is `escalated` to specialist review with the reason "provider unavailable",
   and no code is given.

A loop-budget overrun also escalates. An unexpected error marks the run `failed` with no result. In no case is an
incomplete run returned as a confident answer.

**Demo (item 8):**

```bash
curl -X POST localhost:8001/admin/mode -H 'Content-Type: application/json' -d '{"mode":"down"}'
# submit runs: the 1st retries, the breaker opens, later runs fail fast with "circuit open" and are escalated
curl -X POST localhost:8001/admin/mode -H 'Content-Type: application/json' -d '{"mode":"ok"}'
# after CB_RESET_TIMEOUT_S one trial call goes through (half-open); success closes the breaker
```

The Grafana panel "Circuit breaker state" and the `llm_call` events (they carry `breakers`) show each step.

## 5. Output policy (A3 backstop, L4)

`app/guardrails/output_policy.py` runs after the agents and before storage. It strips an unverified code. It drops
citations that were not retrieved in this run, or whose quote is not in the chunk. It downgrades
`clear-for-filing` unless the code is verified, cited and in chapter 73 or 96. It records every intervention in
`policy_adjustments`.

This is a structural backstop. The agent workstream still owns semantic verification: does the passage actually
support the answer?

## 6. Observability (D5, F8, A6)

* **Logs:** JSON on stdout. Every line has `service`, `correlation_id`, `run_id`, `trace_id` and `span_id`.
* **Traces:** OpenTelemetry, exported over OTLP/HTTP to Jaeger. The API injects the W3C context into the job and
  the worker continues it. As a result, `POST /runs` → `run.execute` → `classifier.<step>` → `tool.<name>` /
  `llm.<agent>.<step>` → HTTP to the provider is one trace.
* **Metrics:** prefixed `ecs_`.
  * Prometheus scrapes every service. Worker replicas are found through Docker DNS.
  * Per-container CPU, memory and uptime come from `process_*` metrics. Availability comes from `up`.
* **Dashboard:** "Export Classification – Overview", provisioned automatically. It is generated by
  `scripts/build_dashboard.py`.
* **Run reconstruction:** `GET /runs/{id}/events` lists every step, tool call (with query and passages) and model
  call (with prompt version, messages, tokens, cost and latency), plus the verification and policy outcome.

## 7. Integrating the agents and retrieval

Set `AGENT_RUNNER=app.agents.<module>:<Class>` in `.env`. The class is built once per worker as
`Class(llm=LLMClient, settings=Settings)`, and the worker calls `run(request: RunRequest, recorder) -> ClassificationResult`.

```python
class ClassificationAgents:
    prompt_version = "classifier-v1+verifier-v1"

    def __init__(self, *, llm, settings):
        self.graph = build_graph()            # LangGraph / CrewAI

    def run(self, request, recorder):
        with recorder.step("classifier", "initial_search"):
            passages = recorder.tool_call("classifier", "corpus_search", search, query, query=query)
            # retrieval tools return [{"chunk_id", "source_id", "text", "score", "locator"}, ...]
        with recorder.step("classifier", "draft"):
            resp = recorder.llm_call("classifier", "draft", messages, prompt_version="classifier-v1")
            # resp.text, resp.prompt_tokens, resp.cost_usd ...
        with recorder.step("verifier", "verify"):
            ...
        return ClassificationResult(...)
```

Rules for agent and retrieval code:
* Call models only through `recorder.llm_call` and tools only through `recorder.tool_call`. That is what gives
  them timeouts, retries, the circuit breaker, cost tracking, spans and the event log.
* Passage-returning tools must return `list[dict]` with `chunk_id` and `text`. Otherwise their citations cannot
  resolve and the output policy withholds the code.
* `LoopBudgetExceeded`, `ProviderUnavailableError` and `ToolTimeoutError` can simply propagate; the worker turns
  them into an escalation. Catch them only to try a different strategy.
* Heavy dependencies go in `requirements/worker.txt`, not in the API image.
* For load tests, the stub can return canned JSON for each agent prompt: see `STUB_RESPONSES_FILE` in
  `app/llm_stub/main.py`, which matches a marker such as `[[stub:classifier]]` in the prompt.

## 8. Caching and rate limiting (D7)

* **Rate limit:** a fixed one-minute window per `X-Client-ID` (or per IP), `RATE_LIMIT_PER_MINUTE`, applied to
  `POST /runs` only. It is a cost control: one client looping on a multi-step agent could exhaust a free-tier
  budget in minutes.
* **Whole-run cache:** `RUN_CACHE_TTL_SECONDS`, off by default.
  * The key is a hash of the normalised request (minus `client_reference`) plus the runner's `prompt_version`.
    Changing a prompt therefore invalidates the cache.
  * Only results produced by the agents are cached. Degraded or failed runs are not.
  * Effect on p95 and on cost per run: still to be measured (planned).

## 9. Verification status

Verified (automated, repeatable):
* `pytest` (unit and integration, no Docker): contracts, validation and quarantine, rate limiting, queue and
  worker, resilience, Redis-outage behaviour, output policy, and static consistency of the dashboard, `.env.example`,
  Compose and Prometheus config.
* **CI** (`.github/workflows/ci.yml`) on every push and PR: builds all images and starts the stack with a single
  `docker compose up --build --wait` from a clean checkout, then runs `scripts/smoke_test.py` with 1 worker and again
  after scaling to 3. The smoke test checks health and readiness, a full run and its event log, quarantine with no
  run created, prompt-injection rejection, the rate limit, the circuit breaker (down, fail-fast, recovery),
  Prometheus targets for 3 replicas, the provisioned Grafana dashboard, and a single Jaeger trace spanning `api` and
  `agent-worker`. Each result appears as an annotation on the PR checks page and in the uploaded `stack-evidence`
  artifact.

Run it locally against a running stack with `python scripts/smoke_test.py --expect-workers 1`.

Not done yet (platform side):
* Measurements: the scaling effect and Locust results against the declared SLO (procedure and tooling in
  `docs/loadtest.md`; waiting for the team to declare the SLO and for the real agents), and the cache effect.
* ~~Feedback endpoint (L6)~~: done by the web-client workstream. `POST /runs/{id}/feedback`,
  `GET /feedback/summary` ([`api-contract.md`](api-contract.md#feedback-l6)), and the `web` service (browser
  client + same-origin `/api/` proxy). `/ready` now also reports `workers_alive` (INC-001). Please review.
* Real provider configuration (free tier) once the agents are integrated.
