# Load test report

SLO (DECLARED, declared 2026-11-15 by NIKHIL (PES1UG24AM311), reporting team approval): complete-run p95 <= 60 s, POST /runs p95 <= 300 ms, failure rate < 1%.
A *complete run* is measured from `POST /runs` until the run reaches a terminal status.

| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| 3 | 5 | 275 | 0 | 0.91 | 3.5 | 4.5 | 5.0 | 5 | met | inconclusive (see queue wait and worker busy ratio) |

## Concurrency at which the target fails

- **3 worker(s):** target met at every tested level (up to 5 users); the failure point was not reached, so test higher concurrency before claiming a limit.
