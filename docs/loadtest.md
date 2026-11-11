# Load test procedure (D6, A7, D7)

Owner: platform workstream. Target and rationale: [`slo.md`](slo.md). Machine-readable target: `loadtest/slo.json`.

## 0. Declare the SLO first

The brief requires the p95 target to be set **before** measuring anything. `scripts/run_loadtest.py` enforces
this: it refuses to run while `loadtest/slo.json` has `"status": "PROPOSED"`.

1. The team agrees the numbers in `docs/slo.md`.
2. Set `"status": "DECLARED"`, `"declared_at"` (date) and `"declared_by"` in `loadtest/slo.json`.
3. Commit that change. The commit date is the evidence that the target preceded the measurement.

## 1. Run

```bash
cp .env.example .env            # Windows: Copy-Item .env.example .env
docker compose up -d --build --wait
python scripts/run_loadtest.py --workers 1,3 --users 5,10,20,40 --duration 180
```

For each (workers, users) pair the script scales `agent-worker`, waits for the queue to drain, runs Locust for
`--duration` seconds against the stub LLM, and queries Prometheus for server-side figures over the same window.
It then writes `loadtest/results/report.md`. Expect `len(workers) x len(users) x ~3.5 min` (about 25 min for the
default matrix).

Live view while a test runs: Grafana <http://localhost:3000> (dashboard "Export Classification – Overview"),
Locust web UI alternative: `docker compose --profile loadtest up locust` then <http://localhost:8089>.

## 2. Conditions that make the result valid

* **Stub, not a provider.** `LLM_BASE_URL` must point at `llm-stub` (the default). Keep
  `STUB_LATENCY_MS` at a realistic figure (1500 ms by default) so run time resembles a real model.
* **Agent under test.** Results describe whatever `AGENT_RUNNER` is configured. Until the real agents are
  integrated this is the stub agent (1 model call per run). **Re-run the matrix with the real agents before using the
  numbers in the report**, because the real agent makes several model calls per run.
* **Rate limit.** Each simulated user has its own `X-Client-ID`, so the per-client limit is not what limits
  throughput. If it ever does (HTTP 429 in Locust failures), raise `RATE_LIMIT_PER_MINUTE` and note it.
* **Cache off.** Keep `RUN_CACHE_TTL_SECONDS=0` for the SLO runs; measure the cache separately (section 4).
* Record: commit SHA, `.env` values that differ from `.env.example`, machine CPU/RAM, Docker resource limits.
* The `complete_run` figure has about 0.5 s of polling granularity (`LOADTEST_POLL_INTERVAL_S`).
  That is part of what a polling client experiences.

## 3. What the report contains

* RPS, p50, p95, p99 of a complete run, and POST /runs p95, per (workers, users), each marked met / MISSED
  against the declared SLO.
* The **concurrency at which the target first fails** per worker count, and the **component responsible**.
  The component comes from server-side data: queue wait (p95), worker busy ratio and API p95.
  * Queue wait high and workers about 100 % busy: **agent-worker capacity**.
  * API p95 high: the **API**.
  * Queue wait small: **run execution time** (model/stub latency and agent steps).
* The **effect of scaling** (first vs last worker count): throughput ratio and p95 at the same concurrency.
  If throughput does not rise with workers, the bottleneck column names what limits it instead.

## 4. Cache and cost (D7)

1. Baseline: the matrix above with `RUN_CACHE_TTL_SECONDS=0`.
2. Repeat one step (for example 3 workers, 20 users) with `RUN_CACHE_TTL_SECONDS=3600` and
   Locust's repeated descriptions (six distinct inputs in `loadtest/locustfile.py`), then compare p95 and
   the `ecs_llm_cost_usd_total` per run on the Grafana "Cost per 100 runs" panel.
3. Report cache hit ratio (`ecs_run_cache_total`) next to the p95 change.

## 5. Keeping the evidence

`loadtest/results/` is git-ignored because it is scratch space. When a run is final, copy `report.md`, the
`*_stats.csv` and `*_prom.json` files to `docs/evidence/loadtest/` and commit them.

## 6. Checking the tooling without Docker

```bash
python -m pytest tests/platform/test_loadtest_report.py
```

covers CSV parsing, the SLO verdicts (including boundaries), the first-failure search, bottleneck inference and the
refusal to measure before the SLO is declared.
