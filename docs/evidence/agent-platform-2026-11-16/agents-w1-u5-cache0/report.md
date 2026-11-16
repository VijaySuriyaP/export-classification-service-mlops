# Load test report

SLO (DECLARED, declared 2026-11-15 by NIKHIL (PES1UG24AM311), reporting team approval): complete-run p95 <= 60 s, POST /runs p95 <= 300 ms, failure rate < 1%.
A *complete run* is measured from `POST /runs` until the run reaches a terminal status.

| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| 1 | 5 | 103 | 0 | 0.33 | 13.0 | 15.0 | 15.0 | 5 | met | agent-worker capacity (runs wait in the queue while workers are busy) |

## Concurrency at which the target fails

- **1 worker(s):** target met at every tested level (up to 5 users); the failure point was not reached, so test higher concurrency before claiming a limit.
