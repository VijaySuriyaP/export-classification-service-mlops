# Load test report

SLO (DECLARED, declared 2026-11-15 by NIKHIL (PES1UG24AM311), reporting team approval): complete-run p95 <= 60 s, POST /runs p95 <= 300 ms, failure rate < 1%.
A *complete run* is measured from `POST /runs` until the run reaches a terminal status.

| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| 1 | 5 | 203 | 0 | 0.67 | 5.5 | 6.6 | 7.1 | 5 | met | inconclusive (see queue wait and worker busy ratio) |
| 1 | 20 | 221 | 0 | 0.67 | 28.0 | 30.0 | 31.0 | 6 | met | inconclusive (see queue wait and worker busy ratio) |
| 1 | 50 | 253 | 0 | 0.68 | 71.0 | 74.0 | 75.0 | 17 | MISSED | inconclusive (see queue wait and worker busy ratio) |
| 1 | 100 | 298 | 0 | 0.66 | 148.0 | 152.0 | 152.0 | 34 | MISSED | inconclusive (see queue wait and worker busy ratio) |
| 3 | 5 | 391 | 0 | 1.29 | 2.0 | 2.5 | 3.0 | 5 | met | inconclusive (see queue wait and worker busy ratio) |
| 3 | 20 | 610 | 0 | 1.97 | 8.1 | 9.1 | 9.6 | 6 | met | inconclusive (see queue wait and worker busy ratio) |
| 3 | 50 | 648 | 0 | 2.01 | 23.0 | 24.0 | 25.0 | 6 | met | inconclusive (see queue wait and worker busy ratio) |
| 3 | 100 | 699 | 0 | 2.00 | 48.0 | 49.0 | 50.0 | 28 | met | inconclusive (see queue wait and worker busy ratio) |

## Concurrency at which the target fails

- **1 worker(s):** first missed at **50 concurrent users** (p95 74.0 s, 0.0% failed). Responsible: inconclusive (see queue wait and worker busy ratio).
- **3 worker(s):** target met at every tested level (up to 100 users); the failure point was not reached, so test higher concurrency before claiming a limit.

## Effect of scaling (1 -> 3 workers)

| Users | RPS before | RPS after | Throughput x | p95 before (s) | p95 after (s) |
|---:|---:|---:|---:|---:|---:|
| 5 | 0.67 | 1.29 | 1.94 | 6.6 | 2.5 |
| 20 | 0.67 | 1.97 | 2.93 | 30.0 | 9.1 |
| 50 | 0.68 | 2.01 | 2.96 | 74.0 | 24.0 |
| 100 | 0.66 | 2.00 | 3.04 | 152.0 | 49.0 |

If throughput does not rise roughly with the worker count, the table above names the component that is limiting instead (queue wait low and API p95 high means the API; queue wait low and workers idle means the load generator or the stub).
