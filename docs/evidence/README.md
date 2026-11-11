# Evidence

Genuine output from the running system. **No mock screenshots.** Captured on 2026-11-11 on Windows 11 + Docker
Desktop 29.8.2 from branch `feature/web-client-feedback-docs` (base `29bf8ad`), with the **stub agent runner**
(`stub-v0`, which always escalates). Files with `-stub` in the name must be re-captured once the real agents are
integrated: `python scripts/capture_screenshots.py --tag agents-v1`.

| File | What it shows | Test IDs |
|---|---|---|
| `pytest.txt` | Unit/integration suite result | U-01 |
| `compose-ps-1-worker.txt`, `compose-ps-3-workers.txt` | All services healthy; 3 worker replicas | S-01, S-10 |
| `smoke-1-worker.txt` | Full smoke test, 9/9 | S-01…S-09 |
| `smoke-3-workers.txt` | Smoke test after `--scale agent-worker=3` | S-10 |
| `drill-provider-down.txt` | RB-1: breaker opens, runs escalate with no code, recovery | D-01, demo #8 |
| `drill-redis-outage.txt` | RB-2: clean 503, worker back-off, recovery | D-02, W-10 |
| `drill-worker-backlog.txt` | RB-3: runs stall with no workers; `/ready` still "ready" (INC-001) | D-03 |
| `drill-worker-backlog-after-fix.txt` | INC-001 fix: `workers_alive` 3 → 0 | D-03 |
| `drill-api-down-web-proxy.txt` | RB-4: web stays up, 502 on `/api`, recovers with no restart | D-04, W-11 |
| `deeplink-run-id.txt` | Run ID used for the deep-link screenshot | W-07 |
| `screenshots/web-valid-run-stub.png` | Submit → finished run, route, verification, usage, event log | W-01, W-02, demo #2/#3 |
| `screenshots/web-malformed-quarantined-stub.png` | Inline field errors + quarantine ID, no run | W-03, demo #4 |
| `screenshots/web-cross-field-quarantined-stub.png` | Cross-field error under both fields | W-04 |
| `screenshots/web-prompt-injection-rejected-stub.png` | Guardrail rejection | W-05, demo #7 |
| `screenshots/web-feedback-recorded-stub.png` | Feedback sent; quality summary by prompt version | W-06 |
| `screenshots/web-deeplink-run-stub.png` | Opening a run by `?run=` link | W-07 |
| `screenshots/web-no-workers-warning.png` | Red "No workers running" pill | W-08, INC-001 |
| `screenshots/web-mobile-390px-stub.png` | Phone-width layout | W-09 |

Still to capture (screenshots of live Grafana and Jaeger UIs and the Locust report) after the agents land; see
[`../demo-checklist.md`](../demo-checklist.md).
