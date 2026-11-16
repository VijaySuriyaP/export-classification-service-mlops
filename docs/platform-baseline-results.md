# Platform baseline load/scaling results

Owner: NIKHIL (PES1UG24AM311). Measurement date: 2026-11-15.

## Provenance and scope

- [GitHub Actions run 43738172426](https://github.com/VijaySuriyaP/export-classification-service-mlops/actions/runs/43738172426).
- Tested commit: `f341d81d27c4021221b450f7496d88e1d3400ef8`.
- SLO declaration commit: `0ae73ce`, preceding measurement.
- Raw evidence: [`evidence/platform-baseline-2026-11-15/`](evidence/platform-baseline-2026-11-15/).
- Eight isolated Docker Compose stacks on GitHub-hosted Linux runners; four logical CPUs
  were reported in each saved conditions file. Host specifications are included per case.
- Five-minute measurement after ramp-up, with up to 180 seconds allowed for in-flight
  users to finish; therefore CSV totals include a completion tail, not exactly 300 seconds.
- `StubAgentRunner`, **one model call per run**; local LLM stub with 1500 ms mean delay
  and configured 500 ms jitter. Cache off. Polling interval 0.5 seconds.
- No Cohere calls and no classification-correctness claim. This is **not real-agent capacity**.

## Results

| Workers | Users | Complete-run RPS | p50 (s) | p95 (s) | Failed runs | SLO |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 5 | 0.67 | 5.5 | 6.6 | 0 | met |
| 1 | 20 | 0.67 | 28.0 | 30.0 | 0 | met |
| 1 | 50 | 0.68 | 71.0 | 74.0 | 0 | missed |
| 1 | 100 | 0.66 | 148.0 | 152.0 | 0 | missed |
| 3 | 5 | 1.29 | 2.0 | 2.5 | 0 | met |
| 3 | 20 | 1.97 | 8.1 | 9.1 | 0 | met |
| 3 | 50 | 2.01 | 23.0 | 24.0 | 0 | met |
| 3 | 100 | 2.00 | 48.0 | 49.0 | 0 | met |

All cases had submission p95 below the 300 ms target (5–34 ms).
The complete-run target is p95 <= 60 seconds, with failed runs strictly below 1%.
All eight jobs successfully executed; that is distinct from an SLO pass.

## Scaling and failure point

- One worker first missed the target at **50 tested concurrent users**.
  The boundary lies between the tested levels 20 and 50; its exact location was not measured.
- Three workers met the target through **100 users**. The failure point was **not reached**.
- At 50 users, throughput increased about **2.96x** and p95 fell from **74 s to 24 s**.
- At 100 users, throughput increased about **3.04x** and p95 fell from **152 s to 49 s**.

## Bottleneck assessment and measurement limitations

Worker execution capacity is the likely limiting component: one worker plateaus near 0.67 RPS
(consistent with one serial 1.5-second stub call), three workers near 2 RPS, while submission
remains fast and queueing rises. This is an inference from the scaling and latency evidence,
not a definitive component diagnosis from the original busy-ratio metric.

The original automatically generated report marks bottlenecks **inconclusive**. Its
`worker_busy_ratio` query averaged gauges from *all services*: the API also registers this
metric at zero, diluting the value. The query has now been restricted to `job='agent-worker'`
for future runs. Historical sidecars and the original report are preserved unchanged, not
retroactively corrected or presented as new measurements.

Prometheus histogram quantiles are bucket approximations, whereas the headline values here
come from Locust. Sidecars are queried after user shutdown, so their window can include a
completion/drain tail; they do not exactly match the steady-state measurement window.
Separate hosted runners introduce hardware/load variation. Resource files are end-of-run
snapshots, not peak resource measurements. Availability >=99% was not independently measured.

## Still required for the final platform report

1. A representative integrated-agent workload with model calls replaced by controlled
   stub responses (not a single-call stub-agent capacity extrapolation).
2. Cache-on versus cache-off measurements and model-call/token cost comparison.
3. If a failure concurrency is claimed for three workers, extend the tested range until
   a miss is observed; otherwise explicitly report that the limit was not reached.
