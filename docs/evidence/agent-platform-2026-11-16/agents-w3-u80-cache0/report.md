# Load test report

SLO (DECLARED, declared 2026-11-15 by NIKHIL (PES1UG24AM311), reporting team approval): complete-run p95 <= 60 s, POST /runs p95 <= 300 ms, failure rate < 1%.
A *complete run* is measured from `POST /runs` until the run reaches a terminal status.

| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| 3 | 80 | 374 | 0 | 0.99 | 79.0 | 81.0 | 82.0 | 21 | MISSED | agent-worker capacity (runs wait in the queue while workers are busy) |

## Concurrency at which the target fails

- **3 worker(s):** first missed at **80 concurrent users** (p95 81.0 s, 0.0% failed). Responsible: agent-worker capacity (runs wait in the queue while workers are busy).
