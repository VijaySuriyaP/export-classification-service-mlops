# Service level objective — declared before any measurement (D6)

**Status:** PROPOSED by the platform workstream on 2026-11-10. It needs team sign-off before the first load test.
No load test has been run yet. Once the team agrees, this target must not change after results are seen.

## Objective

> **p95 of a complete run (time from `POST /runs` until `GET /runs/{id}` returns a terminal status) ≤ 60 seconds,
> measured over a 5-minute load test against the LLM stub.**

Secondary targets:
* `POST /runs` p95 ≤ 300 ms
* failed runs < 1 %
* availability of `/ready` ≥ 99 % during the test

## Justification (brief §1)

* **The task is not interactive.** A trade compliance analyst classifies a shipment as one step in preparing
  documents, and a manual classification takes minutes. A result within a minute keeps the analyst working on the
  same item, which is the value of the tool. Seconds would not improve the workflow meaningfully.
* **The cost of an error is high.** Misclassification can get the consignment held at customs and attract
  penalties, so the target deliberately leaves time for the agent's multi-step loop. That loop is: search → look
  for modifying notes → search again → verify → possibly retry with a different strategy. A tighter target would
  push us towards a single-pass pipeline, which the brief shows fails the vacuum-flask case.
* **It is consistent with the loop bounds.**
  * Estimated budget: about 6 model calls at roughly 1.5 s each (stub delay `STUB_LATENCY_MS=1500`), about 5
    retrieval calls at under 1 s each, and overhead. That is about 15 s of execution.
  * This leaves room for queue wait under load.
  * The hard wall-clock bound (`RUN_WALL_CLOCK_TIMEOUT_S=120`) is twice the target, so a run that exceeds the
    bound is always an SLO miss and is escalated, never answered.

## Test conditions to record with the results

* Commit SHA.
* `.env` values: stub latency, loop limits, cache on or off, rate limit (raised or disabled for the load client).
* Number of worker replicas (1 and 3).
* Locust users and spawn rate, and test duration.
* Machine (CPU and RAM) and Docker resource limits.

## What will be reported

* RPS.
* p50 and p95 of `complete_run` against the target, and whether it was met.
* The concurrency at which the p95 target first fails, and the component responsible, using these signals:
  * queue depth and queue wait (worker-bound);
  * worker busy ratio;
  * API latency (API-bound);
  * Redis CPU;
  * stub latency.
* The same figures with `--scale agent-worker=3`.
