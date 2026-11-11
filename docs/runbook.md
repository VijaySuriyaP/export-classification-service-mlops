# Runbook: Export Classification Check Service

Owner: web client, docs and test coordination (Vijay Suriya P). Covers requirement **D8**. The platform design is in
[`platform.md`](platform.md) and the incident history is in [`incident-log.md`](incident-log.md).

RB-1 to RB-4 were **rehearsed on the local Compose stack on 2026-11-11** with the stub agent runner. The output of
each drill is in `docs/evidence/` and is linked from each section. Steps marked *(not rehearsed)* are standard
procedure that was not exercised. Commands assume the default ports from `.env.example`.

## 0. First five minutes: triage

```bash
docker compose ps                                   # anything not "Up (healthy)"?
curl -s localhost:8000/ready                        # {"status","redis","queue_depth","workers_alive"}
curl -s localhost:8001/admin/mode                   # LLM stub mode (only when the stub is the provider)
docker compose logs --since 5m --no-log-prefix api agent-worker | grep -E '"level": "(ERROR|WARNING)"'
```

| What you see | Most likely cause | Go to |
|---|---|---|
| Runs end `escalated` with reason "Model provider unavailable…", and no code is returned | Model provider down, or the circuit breaker is open | [RB-1](#rb-1-model-provider-unavailable--circuit-breaker-open) |
| `POST /runs` returns `503 service_unavailable`, `/ready` returns `503` | Redis down | [RB-2](#rb-2-redis-queue--state-store-unavailable) |
| Runs stay `queued`, `queue_depth` grows, `workers_alive: 0`, web client shows a red "No workers running" pill | No live agent-worker | [RB-3](#rb-3-runs-stuck-in-queued-no-workers--backlog) |
| The web page loads but says "API unreachable" or "web proxy could not reach the API" (502/504) | `api` container down or restarting | [RB-4](#rb-4-web-client-cannot-reach-the-api) |
| Many `429 rate_limited` responses | One client over `RATE_LIMIT_PER_MINUTE` | [§5](#5-other-quick-fixes) |

Grafana (<http://localhost:3000>, dashboard "Export Classification – Overview") has a panel for each signal named
below.

---

## RB-1. Model provider unavailable / circuit breaker open

**Impact.** No classification is produced. The service fails **safe**: every affected run ends `escalated` to
`specialist-classification-review` with `hs_code: null`. It never guesses. Users see the escalation reason in the
web client.

**Detect**
* Grafana: "Circuit breaker state (0 closed, 1 half-open, 2 open)" = 2; "Model calls by outcome" shows failures;
  "Model call retry rate" rises.
* Prometheus: `max(ecs_circuit_breaker_state) == 2`, `rate(ecs_llm_calls_total{outcome!="success"}[5m])`.
* Logs: `"msg": "circuit breaker transition" … "from": "closed", "to": "open"` and `"model call failed"`.
* Run events (`GET /runs/{id}/events`): an `llm_call` with `outcome=provider_unavailable` and
  `error=primary: circuit open`.

**Diagnose**
1. Is it the provider or us? Call the provider directly with the same base URL and key the worker uses
   (`LLM_BASE_URL`). For the stub: `curl -s localhost:8001/admin/mode` and `curl -s localhost:8001/health`.
2. Real provider: check whether it returns `429` (quota or free-tier limit), `401` (key) or `5xx`/timeouts. The
   `error` text in the `llm_call` event shows the HTTP status of each attempt.
3. Check that the breaker settings are sane: `CB_FAILURE_THRESHOLD`, `CB_RESET_TIMEOUT_S`, `LLM_TIMEOUT_S`.

**Mitigate**
* Provider outage: nothing to restart. The breaker probes every `CB_RESET_TIMEOUT_S` (half-open) and closes by
  itself on the first success.
* *(not rehearsed: no fallback provider is configured yet)* If a fallback provider is available, set `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY` and `LLM_FALLBACK_MODEL`
  in `.env`, then `docker compose up -d agent-worker`. Calls then go primary → fallback (`fallback_used: true` in
  events).
* Bad key or quota: fix `.env`, then `docker compose up -d agent-worker` (this recreates the container with the new
  environment).
* Demo or stub: `curl -X POST localhost:8001/admin/mode -H 'Content-Type: application/json' -d '{"mode":"ok"}'`.
* Escalated runs are **not** retried automatically. Ask users to resubmit once the breaker is closed (feedback
  reason `other` can flag them).

**Verify recovery.** Logs show `open -> half_open` and then `half_open -> closed`. A new run has an `llm_call` with
`outcome=success`. Or run `python scripts/smoke_test.py --only run`.

**Evidence:** [`evidence/drill-provider-down.txt`](evidence/drill-provider-down.txt) (4 runs escalated with no code;
breaker closed → open → half-open → open; recovery: half-open → closed, model call succeeded).

---

## RB-2. Redis (queue + state store) unavailable

**Impact.** No new runs can be submitted or polled. The API fails **fast and clean**: `503` with `Retry-After: 5`
and `"no run was created or changed"`. `/health` stays `200`, so Docker does not restart-loop the API. Workers back
off and resume by themselves. Persisted runs, quarantine and feedback survive a restart (AOF is enabled).

**Detect**
* `curl -s localhost:8000/ready` returns `503 {"status":"not_ready","redis":"Error … connecting to redis:6379 …"}`.
* Grafana: "Availability (scrape up)" shows `redis` down; `ecs_redis_errors_total` rises.
* Worker logs: `"msg": "redis unavailable; backing off"` with an increasing `retry_in_s`.
* Web client: red "API not ready (503)" pill; submitting shows "Service unavailable (queue backend down)".

**Diagnose**
```bash
docker compose ps redis
docker compose logs --tail 50 redis          # OOM? AOF corruption? disk full?
docker system df                             # disk space for the redis-data volume
```

**Mitigate**
```bash
docker compose start redis                   # or: docker compose up -d redis
```
* *(not rehearsed)* AOF corruption reported in the logs: stop Redis, back up the volume, and run `redis-check-aof --fix` inside a
  one-off container on the `redis-data` volume. Escalate to the platform owner before deleting any data.
* A run that was mid-execution when Redis disappeared is requeued by its worker once Redis returns. Bounded by
  `RUN_MAX_ATTEMPTS`; terminal runs are skipped.

**Verify recovery.** `/ready` returns `{"status":"ready",…}`, then run `python scripts/smoke_test.py --only ready,run`.

**Evidence:** [`evidence/drill-redis-outage.txt`](evidence/drill-redis-outage.txt) (health 200, ready 503, submit
503 + Retry-After, worker back-off, recovery and passing smoke). Feedback persisted across the restart: first lines
of [`evidence/drill-worker-backlog.txt`](evidence/drill-worker-backlog.txt).

---

## RB-3. Runs stuck in `queued` (no workers / backlog)

**Impact.** Submissions are accepted (`202`) but nothing progresses. Before INC-001 this was **silent**: `/ready`
said `ready`. `/ready` now reports `workers_alive`, and the web client turns its status pill red.

**Detect**
* `curl -s localhost:8000/ready` returns `"workers_alive": 0` and a growing `queue_depth`.
* Grafana: "Queue depth" rising, "Busy workers" = 0, "Availability (scrape up)" shows no `agent-worker` targets.
  "Queue wait p95" rises when workers exist but are too few.
* Prometheus: `count(up{job="agent-worker"} == 1)`.

**Diagnose**
```bash
docker compose ps agent-worker                 # exited? restarting? unhealthy?
docker compose logs --tail 100 agent-worker    # import error in AGENT_RUNNER? missing model files? OOM?
```
* Workers keep restarting: usually a bad `AGENT_RUNNER` path or a crash at startup in agent or retrieval code. The
  last lines before the exit name the exception.
* Workers are up but the queue still grows: the load exceeds capacity. Compare "Queue wait p95" with
  "Run latency p95".

**Mitigate**
```bash
docker compose up -d agent-worker                          # start or recreate
docker compose up -d --scale agent-worker=3 --no-recreate  # add capacity (A7)
```
* A startup crash after an agent change: set `AGENT_RUNNER` back to the last working runner (in the worst case
  `app.agents.stub_runner:StubAgentRunner`, which always escalates and never classifies) and restart the workers.
  Tell the agent owner.
* Jobs held by a worker that died mid-run are requeued automatically once its heartbeat expires
  (`WORKER_HEARTBEAT_TTL_S`, 30 s). No manual action is needed.

**Verify recovery.** `workers_alive` ≥ 1, `queue_depth` drains to 0, and the queued runs reach a terminal status.

**Evidence:** [`evidence/drill-worker-backlog.txt`](evidence/drill-worker-backlog.txt) (3 runs queued with no
workers, queue depth 3 in Prometheus, all 3 finished after restart) and
[`evidence/drill-worker-backlog-after-fix.txt`](evidence/drill-worker-backlog-after-fix.txt) (`workers_alive`
3 → 0), plus [`evidence/screenshots/web-no-workers-warning.png`](evidence/screenshots/web-no-workers-warning.png).

---

## RB-4. Web client cannot reach the API

**Detect.** The page loads (nginx is up) but the pill says "API unreachable", or actions report "The web proxy could
not reach the API service" (502/504).

```bash
docker compose ps api web
curl -s localhost:8000/health        # direct to the API
curl -s localhost:8080/api/health    # through the web proxy
curl -s localhost:8080/healthz       # nginx itself
```

* Direct works but the proxy fails: `docker compose logs web` (an upstream error), then `docker compose restart web`.
  nginx re-resolves `api` through Docker DNS every 10 s, so a recreated API container should need no web restart.
  In the drill the API came back on the same IP, so recovery without a restart was verified but the IP-change path
  was not exercised.
* **Evidence:** [`evidence/drill-api-down-web-proxy.txt`](evidence/drill-api-down-web-proxy.txt) (`/healthz` 200
  while `/api/health` returned 502; after `--force-recreate` of the API the proxy returned 200 with no web restart).
* Direct fails: the API is down. `docker compose logs api` usually shows `REDIS_URL is not set` or an import error.
  Then `docker compose up -d api`.

## 5. Other quick fixes

| Symptom | Fix |
|---|---|
| `429 rate_limited` for a legitimate client | Each browser gets its own `X-Client-ID`, so one tab cannot block others. For load tests raise `RATE_LIMIT_PER_MINUTE` or set `RATE_LIMIT_ENABLED=false` in `.env`, then `docker compose up -d api`. |
| Port already in use on `docker compose up` | Change `WEB_PORT`, `API_PORT`, etc. in `.env`. |
| Grafana shows "No data" | Check Prometheus targets at <http://localhost:9090/targets>. A new metric appears only after its first event. |
| Need the full story of one run | `GET /runs/{id}/events`, the "Run reconstruction" panel in the web client, or Jaeger at <http://localhost:16686> using the run's `trace_id`. |
| Clean slate (destroys all runs, quarantine and feedback) | `docker compose down -v`. Ask the team first. |
