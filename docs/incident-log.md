# Incident log

Genuine incidents found during development and testing (requirement **D8**). Each entry records what actually
happened, with evidence. Add new entries at the top, using the template at the end.

---

## INC-001: queued runs stall silently when no agent-worker is running

| | |
|---|---|
| **Date** | 2026-11-11 |
| **Found by** | Vijay Suriya P, rehearsing runbook drill RB-3 on the local Compose stack (branch `feature/web-client-feedback-docs`, base `29bf8ad`) |
| **Severity** | Medium. No wrong answer was ever returned, but users wait with no signal, and operators get a false "ready". |
| **Status** | Mitigated (detection). Follow-ups open. |

**What happened.** With every `agent-worker` stopped (`docker compose stop agent-worker`), three submitted runs
stayed `queued` and `queue_depth` rose to 3. At the same time `GET /ready` returned
`{"status":"ready","redis":"ok","queue_depth":3}`. Nothing in the API response or the web client showed that the
runs would never progress. The only signal was the Prometheus `up{job="agent-worker"}` target disappearing, and
nobody looks at that during a demo. After the workers restarted, all three runs finished normally
(`attempts=1`). The queue design worked; detection did not.

**Timeline (UTC)**
* 04:57:39: drill started; all workers stopped; 3 runs submitted.
* 04:57:42: all 3 runs `queued`; `/ready` reports `ready`, `queue_depth: 3`.
* 04:57:54: Prometheus `max(ecs_queue_depth)` = 3.
* ~04:58: workers restarted; all 3 runs `escalated` (stub runner) within seconds; queue drained to 0.
* 04:59: fix deployed locally and verified (below).

**Root cause.** `/ready` checks only the API's own dependency (Redis). That is correct for readiness, because the
API *can* accept and queue work. But nothing exposed whether any consumer existed. Workers already write a
heartbeat key (`ecs:worker:<id>:alive`, TTL `WORKER_HEARTBEAT_TTL_S`), but only the dead-worker reaper read it.

**Fix.**
* `GET /ready` now also returns `workers_alive`, the count of unexpired heartbeat keys. Its status semantics are
  unchanged, so a worker outage does not mark the API unready or break the Compose health checks.
* The web client turns its status pill red ("No workers running · N queued"), with a tooltip pointing to RB-3.
* `scripts/smoke_test.py` `ready` check now fails if no worker is alive.
* Regression test: `tests/platform/test_api.py::test_ready_reports_live_workers_without_changing_status`.
* Runbook RB-3 written from this drill.

**Verification.** [`evidence/drill-worker-backlog-after-fix.txt`](evidence/drill-worker-backlog-after-fix.txt):
`workers_alive: 3`, then all workers stopped and after the 30 s heartbeat TTL `workers_alive: 0`.
Screenshot: [`evidence/screenshots/web-no-workers-warning.png`](evidence/screenshots/web-no-workers-warning.png).

**Evidence of the incident:** [`evidence/drill-worker-backlog.txt`](evidence/drill-worker-backlog.txt).

**Follow-ups**
* [ ] Platform owner (NIKHIL): review the `/ready` change; consider a Grafana alert or stat panel on
  `count(up{job="agent-worker"} == 1) == 0` with `ecs_queue_depth > 0`.
* [ ] The detection lag equals `WORKER_HEARTBEAT_TTL_S` (30 s) after a hard stop. Acceptable for now; revisit if
  the TTL changes.
* [ ] Found while verifying the fix: after `docker compose up --build` replaced the worker, `/ready` showed
  `workers_alive: 2` with one worker running, dropping to 1 within 30 s. A gracefully stopping worker does not
  delete its own `ecs:worker:<id>:alive` key, so the count over-reports for up to one TTL after a restart. Proposed
  fix for the platform owner: delete the key in the worker's shutdown path (`app/worker/worker.py`).

---

## Template

```markdown
## INC-NNN: <one-line symptom>

| | |
|---|---|
| **Date** | YYYY-MM-DD |
| **Found by** | <who, doing what: demo rehearsal, load test, CI, …> |
| **Severity** | Low / Medium / High: <user impact in one line> |
| **Status** | Open / Mitigated / Resolved |

**What happened.** Facts only: commands, responses, run IDs.
**Timeline (UTC).**
**Root cause.**
**Fix.** Code/config change, with the test that now guards it.
**Verification.** Link to evidence in docs/evidence/.
**Follow-ups.** Checkboxes with owners.
```
