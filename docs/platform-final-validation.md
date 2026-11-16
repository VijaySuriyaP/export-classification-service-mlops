# Final platform validation

Owner: NIKHIL (PES1UG24AM311).

## Recorded results (2026-11-16)

Completed [Actions run 43795668764](https://github.com/VijaySuriyaP/export-classification-service-mlops/actions/runs/43795668764)
at tested commit `6370648`. All seven measurement jobs and the combined-report job completed.
The premeasurement SLO declaration is unchanged (`0ae73ce`, 2026-11-15).

Full generated report and raw evidence:
[`evidence/agent-platform-2026-11-16/`](evidence/agent-platform-2026-11-16/).
The original report and counter sidecars are retained; verbose logs remain in the Actions artifact.

| Workers | Users | Client attempts finished | Client failures | p95 (s) | SLO |
|---:|---:|---:|---:|---:|---|
| 1 | 5 | 103 | 0 | 15 | met |
| 1 | 20 | 118 | 0 | 61 | missed |
| 1 | 80 | 198 | 137 | 180 | missed |
| 3 | 5 | 275 | 0 | 4.5 | met |
| 3 | 20 | 317 | 0 | 20 | met |
| 3 | 80 | 374 | 0 | 81 | missed |

The first **tested** failure level is 20 users with one worker, and 80 with three.
Exact boundaries are only bracketed (5–20 and 20–80); this does not identify an exact
maximum user count. At 20 users, scaling increases throughput approximately 3.04x
and reduces p95 from 61 to 20 seconds.

**Do not interpret the overloaded 1-worker/80-user RPS as successful throughput.**
137 of its 198 completed attempts were client timeouts after 180 seconds, not completed
classification responses. The reported 0.41 attempts/s includes these failures, and its
180-second p95 is timeout-censored, not a p95 of successful run completion. The failure
CSV records that cause. Requests that time out client-side are not automatically cancelled
on the server, which can leave work queued until the isolated stack is torn down.

The worker-capacity diagnosis is supported by the worker-scoped busy ratio (about 96%
at 3 workers/20 users), substantial queue wait, fast API submissions (5–29 ms across
cases), and roughly linear scaling at the 20-user load. Prometheus quantiles remain
approximations and the accounting window includes the completion tail.

### Cache and cost result (3 workers, 20 users)

| Setting | p95 | Model calls/server run | Estimated tokens/server run | Hit ratio |
|---|---:|---:|---:|---:|
| Cache off | 20 s | 2 | 4089 | 0% (cache disabled) |
| Prewarmed cache on | 0.51 s | 0 | 0 | 100% |

This is a **97.5% latency reduction** for a best-case repeated identical request,
not an estimate of production hit rate. The accounting counters excluded preflight.
Uncached estimated tokens per run: 3875 prompt + 214 completion.
The classifier contributed 2129 estimated tokens/run; verifier 1960.
Thus the classifier was the larger token contributor (not an observed dollar cost driver).
Actual model-provider spend, including cost per 100 runs, is **USD 0** in both cases.
For nonzero provider prices, a counterfactual estimate per 100 uncached runs is
`387.5 * prompt_USD_per_1K + 21.4 * completion_USD_per_1K`; this formula is not
a claim about Cohere billing. Stub tokens are character-based estimates.

## Platform deliverable status

- API contracts, validation/quarantine, asynchronous queue and polling: implemented and tested.
- Timeouts, retries/backoff, circuit breaker, safe degradation: implemented and covered by CI.
- Worker heartbeat fix: merged; long-running and expired-worker regression tests pass.
- Compose, health checks, logs/correlation IDs, traces, metrics/dashboard: CI stack checks pass.
- SLO, baseline and real two-agent approval-path load/scaling report: recorded above.
- Cache latency and estimated token/cost effects: recorded above.

These remote platform deliverables are ready for team review. This is **not a claim that
the entire project or presentation is verified**. Remaining team checks are listed below.

## Reproducible remote tests

The workflow `.github/workflows/agent-loadtest.yml` uses
`COMPOSE_FILE=compose.yaml:loadtest/compose.agents.yaml` on GitHub Linux runners.
It is a test-only overlay; the normal deployment configuration, agent reasoning,
prompts and retrieval implementation are unchanged.

Each isolated job:

1. Builds the current worker and generates stub responses from text returned by the
   real retrieval adapter for a fixed product.
2. Starts the actual `ClassificationRunner`, Redis queue, API and observability stack.
3. Requires a successful preflight with two model calls, retrieval, handoff and verdict,
   saving the result, trace ID and full event log.
4. Measures a five-minute Locust workload after ramp-up, retaining in-flight users for
   up to 180 seconds. Cache-off cases compare 1/3 workers at 5/20/80 users. A seventh case
   tests a warm 3600-second cache at 3 workers and 20 users.
5. Saves commit/hardware/configuration, CSVs, server metrics and resource snapshots.
   The combined `agent-platform-final-report` artifact contains the performance and
   cache/token/cost report.

The canned approval is a **performance fixture, not a semantic verifier**. This
exercise measures the real two-agent approval path with real retrieval and controlled
model delays. It does not validate tariff codes, follow-up/research traffic, live-provider
rate limits, or classification quality. The baseline single-call test remains separately
documented in `platform-baseline-results.md`.

## Accounting

Worker-only Prometheus counter deltas count finished runs, successful model calls,
estimated stub tokens, configured cost and cache lookups. Preflight counters are excluded.
Totals can differ from client statistics due to the completion tail. Stub token counts are
character-based estimates. Provider spend is zero, so no actual dollar savings are claimed;
the report supplies the formula for a configured priced provider.

## Local reproduction on a Docker-capable machine

Copy `.env.example` to `.env`, then run the same workflow commands with the two Compose
files selected. No API key is required. The overlay pins model traffic to the local stub.
Generated response fixtures and scratch results are git-ignored. Review the workflow
before use; do not apply the performance fixture to a real classification demonstration.

## Acceptance boundary

Remote results can establish platform latency/scaling/cache behaviour for the stated
controlled workload. These cannot establish that a teammate's local deployment has been
updated or that the presentation machine will start correctly.

The team must still perform the final clean-clone demo rehearsal, capture a real classification
trace/dashboard, and validate the exact required product and official citations. Those steps
are not silently marked complete by a green performance workflow.
