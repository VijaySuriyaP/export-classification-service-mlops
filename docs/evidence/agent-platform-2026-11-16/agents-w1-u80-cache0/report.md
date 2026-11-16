# Load test report

SLO (DECLARED, declared 2026-11-15 by NIKHIL (PES1UG24AM311), reporting team approval): complete-run p95 <= 60 s, POST /runs p95 <= 300 ms, failure rate < 1%.
A *complete run* is measured from `POST /runs` until the run reaches a terminal status.

| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| 1 | 80 | 198 | 137 | 0.41 | 180.0 | 180.0 | 181.0 | 29 | MISSED | agent-worker capacity (runs wait in the queue while workers are busy) |

## Concurrency at which the target fails

- **1 worker(s):** first missed at **80 concurrent users** (p95 180.0 s, 69.2% failed). Responsible: agent-worker capacity (runs wait in the queue while workers are busy).
