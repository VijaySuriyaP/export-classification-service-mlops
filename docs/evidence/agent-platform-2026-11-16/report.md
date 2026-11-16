# Real-agent platform performance (scripted model responses)

**Not classification-correctness evidence.** Actual LangGraph classifier/verifier, real FAISS retrieval and platform; canned model responses approve a performance fixture. Two 1.5-second model calls per uncached run. Follow-up/research paths and real-provider rate limits are not measured. Identical inputs are repeated. Cache is prewarmed by a preflight run; this is best-case caching, not a production hit-rate forecast. Hosted hardware differs across isolated jobs. Measurement lasts five minutes after ramp-up plus a completion tail.

# Load test report

SLO (DECLARED, declared 2026-11-15 by NIKHIL (PES1UG24AM311), reporting team approval): complete-run p95 <= 60 s, POST /runs p95 <= 300 ms, failure rate < 1%.
A *complete run* is measured from `POST /runs` until the run reaches a terminal status.

| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| 1 | 5 | 103 | 0 | 0.33 | 13.0 | 15.0 | 15.0 | 5 | met | agent-worker capacity (runs wait in the queue while workers are busy) |
| 1 | 20 | 118 | 0 | 0.33 | 59.0 | 61.0 | 61.0 | 7 | MISSED | agent-worker capacity (runs wait in the queue while workers are busy) |
| 1 | 80 | 198 | 137 | 0.41 | 180.0 | 180.0 | 181.0 | 29 | MISSED | agent-worker capacity (runs wait in the queue while workers are busy) |
| 3 | 5 | 275 | 0 | 0.91 | 3.5 | 4.5 | 5.0 | 5 | met | inconclusive (see queue wait and worker busy ratio) |
| 3 | 20 | 317 | 0 | 1.00 | 18.0 | 20.0 | 21.0 | 5 | met | agent-worker capacity (runs wait in the queue while workers are busy) |
| 3 | 80 | 374 | 0 | 0.99 | 79.0 | 81.0 | 82.0 | 21 | MISSED | agent-worker capacity (runs wait in the queue while workers are busy) |

## Concurrency at which the target fails

- **1 worker(s):** first missed at **20 concurrent users** (p95 61.0 s, 0.0% failed). Responsible: agent-worker capacity (runs wait in the queue while workers are busy).
- **3 worker(s):** first missed at **80 concurrent users** (p95 81.0 s, 0.0% failed). Responsible: agent-worker capacity (runs wait in the queue while workers are busy).

## Effect of scaling (1 -> 3 workers)

| Users | RPS before | RPS after | Throughput x | p95 before (s) | p95 after (s) |
|---:|---:|---:|---:|---:|---:|
| 5 | 0.33 | 0.91 | 2.74 | 15.0 | 4.5 |
| 20 | 0.33 | 1.00 | 3.04 | 61.0 | 20.0 |
| 80 | 0.41 | 0.99 | 2.38 | 180.0 | 81.0 |

If throughput does not rise roughly with the worker count, the table above names the component that is limiting instead (queue wait low and API p95 high means the API; queue wait low and workers idle means the load generator or the stub).

## Cache comparison (3 workers, 20 users)

| Cache | p95 (s) | RPS | Server runs | Model calls/run | Estimated tokens/run | Cache hit ratio | Actual USD/100 runs |
|---|---:|---:|---:|---:|---:|---:|---:|
| off | 20.00 | 1.00 | 320 | 2.000 | 4089.0 | 0.0% | 0.000000 |
| on (warm) | 0.51 | 7.92 | 2414 | 0.000 | 0.0 | 100.0% | 0.000000 |

Warm-cache p95 reduction: **97.5%**. Avoided model calls per completed server run: **2.000**.

The largest uncached token contributor was **classifier**. Stub tokens are estimates from character counts, not provider tokenizer measurements. Actual provider spend is **zero** in both cases; no dollar savings or Cohere cost is claimed. For a priced provider, estimated USD/run = prompt_tokens/run × prompt_USD_per_1K / 1000 + completion_tokens/run × completion_USD_per_1K / 1000, using configured prices at call time.

Accounting uses server counter deltas and includes runs finishing in the completion tail; Locust rows reflect completed client attempts and may differ. Preflight accounting is excluded. Prometheus busy ratio is worker-scoped; its sampling window is after shutdown and can include drain/idle time. Bottleneck inference is heuristic. No service-availability percentage or peak-container-resource claim is made from snapshots. Raw sidecars, conditions, preflight trace/events and CSVs are kept alongside this report.
