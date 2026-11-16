# Final platform validation

Owner: NIKHIL (PES1UG24AM311).

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
