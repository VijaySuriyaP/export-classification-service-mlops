# Test matrix

Owner: Vijay Suriya P (test coordination). Format per README §11: **test ID, precondition/input, steps, expected,
actual, pass/fail, evidence**.

**Last full run:** 2026-11-11, branch `feature/web-client-feedback-docs` (base `29bf8ad`), Windows 11 + Docker
Desktop 29.8.2, `.env` from `.env.example` with the CI timings (`STUB_LATENCY_MS=150`, `LLM_MAX_ATTEMPTS=2`,
`CB_FAILURE_THRESHOLD=3`, `CB_RESET_TIMEOUT_S=5`). Agent runner: **stub** (`stub-v0`), which always escalates and
never classifies. Every "actual" below was observed on that stack; nothing is mocked.

Status key: **PASS**, **FAIL**, **BLOCKED** (cannot run yet; the dependency is named), **NOT RUN**.

How to re-run everything automatable:

```bash
python -m pytest                                                     # unit/integration, no Docker
docker compose up -d --build --wait
python scripts/smoke_test.py                                          # S-01..S-09
docker compose up -d --scale agent-worker=3 --no-recreate --wait
python scripts/smoke_test.py --only run,web,feedback,breaker,scale --expect-workers 3
python scripts/capture_screenshots.py --tag stub                      # W-* screenshots
```

## 1. Automated suites

| ID | Precondition / input | Steps | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|---|
| U-01 | Dev deps installed (`requirements/dev.txt`) | `python -m pytest` | All tests pass | 134 passed | PASS | `evidence/pytest.txt` |
| CI-01 | Push or PR to GitHub | GitHub Actions `unit` + `stack` jobs | Both green; smoke annotations on the PR | Last green on `main` at `29bf8ad`. This branch is not pushed yet. | NOT RUN (this branch) | Actions tab, `stack-evidence` artifact |

## 2. Stack smoke checks (`scripts/smoke_test.py`)

| ID | Precondition / input | Steps | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|---|
| S-01 | Stack up, 1 worker | `--only ready` | `/health` 200, `/ready` ready, ≥ 1 live worker | ready, queue 0, `workers_alive=2` (one stale key, see INC-001 follow-up) | PASS | `evidence/smoke-1-worker.txt` |
| S-02 | Vacuum-flask description | `--only run` | 202 → terminal status; correlation ID kept API → worker; disclaimer; events have step/tool/llm | escalated (stub), e2e 0.20 s, 10 events, trace ID present | PASS | `evidence/smoke-1-worker.txt` |
| S-03 | Web container up | `--only web` | Page + assets served with CSP; `/api/` proxy: ready, submit 202, poll 200 | as expected | PASS | `evidence/smoke-1-worker.txt` |
| S-04 | Finished run | `--only feedback` | 409 before finish, 201 after, 422 on bad rating, summary counts it by prompt version | 409 ✓, 201 ✓, 422 ✓, summary by `stub-v0` | PASS | `evidence/smoke-1-worker.txt` |
| S-05 | `{"product_description":"x","chapter_hint":"84","colour":"red"}` | `--only malformed` | 422 with 3 field errors, in quarantine, `ecs_runs_submitted_total` unchanged | as expected | PASS | `evidence/smoke-1-worker.txt` |
| S-06 | "…Ignore all previous instructions…" | `--only injection` | 422 `prompt_injection_suspected`, no run | as expected | PASS | `evidence/smoke-1-worker.txt` |
| S-07 | 35 submits, one client ID | `--only ratelimit` | 429 after 30 with `Retry-After`; other client still 202 | 429 after 30, Retry-After 17 s | PASS | `evidence/smoke-1-worker.txt` |
| S-08 | Stub switched to `down` | `--only breaker` | Runs escalate with no code; "circuit open" fail-fast; recovery after reset | escalated ×3, open on run 3, recovered on run 1 | PASS | `evidence/smoke-1-worker.txt`, `evidence/drill-provider-down.txt` |
| S-09 | Prometheus, Grafana, Jaeger up | `--only observability` | Targets scraped; dashboard provisioned; one trace spans api + agent-worker | as expected | PASS | `evidence/smoke-1-worker.txt` |
| S-10 | `--scale agent-worker=3` | `--only run,web,feedback,breaker,scale --expect-workers 3` | 3 targets up; work spread over > 1 worker | 3 targets; 3 distinct workers ran 9 runs | PASS | `evidence/smoke-3-workers.txt`, `evidence/compose-ps-3-workers.txt` |

## 3. Web client (manual / browser)

The screenshots come from a real headless Edge typing into the live page (`scripts/capture_screenshots.py`).

| ID | Precondition / input | Steps | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|---|
| W-01 | Stack up | Open <http://localhost:8080> | Page loads; green pill "API ready · N worker(s)"; decision-support banner | as expected | PASS | `evidence/screenshots/web-valid-run-stub.png` |
| W-02 | — | Click "Use the vacuum-flask demo case", add materials + reference, Submit | "Accepted… queued"; timeline Queued → Running → Finished; route card, escalation reason, verification, disclaimer, usage tiles; event log has 10 rows | as expected (stub: Specialist classification review, no code) | PASS | `evidence/screenshots/web-valid-run-stub.png` |
| W-03 | Description `flask`, destination `India`, materials `7up, steel` | Submit | Inline errors under each field; "No run was created (run_created: false)" with the quarantine ID | 3 inline errors, quarantine ID shown | PASS | `evidence/screenshots/web-malformed-quarantined-stub.png` |
| W-04 | Valid description, chapter 73, HS hint 96170012 | Submit | Cross-field error shown under **both** fields | as expected | PASS | `evidence/screenshots/web-cross-field-quarantined-stub.png` |
| W-05 | Prompt-injection text | Submit | "Rejected by the prompt-injection guardrail"; no run | as expected | PASS | `evidence/screenshots/web-prompt-injection-rejected-stub.png` |
| W-06 | Run in progress, then finished | Watch the feedback panel; rate 👎 + "Ignored a note" + code + comment; send | Form hidden until finished; "Thanks, feedback recorded"; summary table updates | as expected | PASS | `evidence/screenshots/web-feedback-recorded-stub.png` |
| W-07 | Existing run ID | Open `/?run=<id>` (also: Recent runs list, "Open a run by ID") | Run loads straight into the result view | as expected (run in `evidence/deeplink-run-id.txt`) | PASS | `evidence/screenshots/web-deeplink-run-stub.png` |
| W-08 | All workers stopped (> 30 s) | Load page | Red pill "No workers running · N queued" | as expected | PASS | `evidence/screenshots/web-no-workers-warning.png` |
| W-09 | 390 px phone width | `capture_screenshots.py --only mobile` | Single column, no horizontal scroll (asserted by the script) | as expected | PASS | `evidence/screenshots/web-mobile-390px-stub.png` |
| W-10 | Redis stopped | Load page, submit | Pill "API not ready (503)"; submit shows "Service unavailable…" | Proxy returned 503 (curl, drill A). The browser message was **not** captured. | PASS (API level) / browser NOT RUN | `evidence/drill-redis-outage.txt` |
| W-11 | API stopped | Load page | Page still loads (`/healthz` 200); pill "API not ready (502)" | `/healthz` 200, `/api/health` 502; recovered with no web restart | PASS (proxy level) | `evidence/drill-api-down-web-proxy.txt` |
| W-12 | > 30 submits in a minute from one browser | Submit repeatedly | "Rate limited: wait N s" | Not exercised in the browser (API behaviour covered by S-07) | NOT RUN | — |
| W-13 | Server text containing HTML | Static check + CSP header | Server text never parsed as HTML; CSP `script-src 'self'` | No HTML-parsing APIs in `web/app.js` (test); CSP header present (S-03) | PASS | `tests/platform/test_config_consistency.py::test_web_client_never_renders_server_text_as_html` |
| W-14 | — | Static check | Every API route the client calls exists | as expected | PASS | `…::test_web_client_only_calls_existing_api_routes` |

## 4. Failure drills (runbook)

| ID | Precondition / input | Steps | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|---|
| D-01 | Stack up | RB-1: stub `down`, 4 runs, stub `ok` | Escalate with no code; breaker opens; half-open probe; closes on success | closed→open→half_open→open→half_open→closed; 4/4 escalated, `hs_code=None` | PASS | `evidence/drill-provider-down.txt` |
| D-02 | Stack up | RB-2: `docker compose stop redis`, probe, start | health 200, ready 503, submit 503 + Retry-After, worker backs off, full recovery | as expected; feedback persisted across restart | PASS | `evidence/drill-redis-outage.txt` |
| D-03 | Stack up | RB-3: stop workers, submit 3, start workers | Runs wait queued and complete after restart; operator can see the cause | Runs completed, but `/ready` said "ready" with no workers → **INC-001**, fixed | PASS after fix | `evidence/drill-worker-backlog.txt`, `evidence/drill-worker-backlog-after-fix.txt` |
| D-04 | Stack up | RB-4: stop API, probe web, recreate API | Web stays up and returns 502 on /api; recovers with no web restart | as expected | PASS | `evidence/drill-api-down-web-proxy.txt` |

## 5. Classification behaviour (needs the real agents and corpus)

These are the tests the stub cannot answer. They become runnable once `AGENT_RUNNER` points at the real agents
(VIKAS) and the retrieval tools index the corpus (Utkarsh). The expected outcomes for the vacuum flask are agreed at
Evaluation 1. The values here are **not** the reference answer.

| ID | Precondition / input | Steps | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|---|
| C-01 | "Stainless steel vacuum flask with a moulded plastic outer body." | Submit via web; open events | Heading that **names the article** found; GRI applied in order; notes considered; citations resolve to retrieved text; not stopped at a steel heading | — | BLOCKED: agents + corpus | — |
| C-02 | Out-of-scope product, e.g. "Laptop computer with aluminium chassis" | Submit | `escalated` with an out-of-scope basis; no code | Stub escalates everything, so a pass now would prove nothing | BLOCKED: agents | — |
| C-03 | Case where the first draft is unsupported | Submit; inspect `verification.strategy_log` + events | Verifier rejects; a different search strategy or escalation follows | — | BLOCKED: agents | — |
| C-04 | Ambiguous after GRI 3 | Submit | `specialist-classification-review` with the competing headings named | — | BLOCKED: agents | — |
| C-05 | Valid request | Open events and the Jaeger trace | Tool calls with queries and passages, prompt version, per-step latency and cost | Works end to end with the stub (1 tool, 1 model call). Needs real retrieval tools to be meaningful. | BLOCKED (partial) | `evidence/screenshots/web-valid-run-stub.png` |
| E-01 | 20-scenario evaluation set | Evaluation CLI | Groundedness, context relevance, faithfulness, hallucination rate; before/after one change | — | BLOCKED: evaluation set + agents | — |
| L-01 | SLO `DECLARED` in `loadtest/slo.json` | `scripts/run_loadtest.py --workers 1,3` | p50/p95 vs target, RPS, failure concurrency, bottleneck, scaling effect | — | BLOCKED: SLO sign-off (team) + agents | `docs/loadtest.md` |

## 6. Observations passed to owners (not failures)

* **API, NIKHIL:** cross-field errors (`hs_code_hint+chapter_hint`, `quantity+quantity_unit`) are reported only when
  every individual field is valid, and only the first cross-field error is returned. A user may fix one error,
  resubmit and meet the next one. This is acceptable under the contract, but noted.
* **Worker, NIKHIL:** a gracefully stopped worker leaves its heartbeat key, so `workers_alive` over-reports for
  up to 30 s (INC-001 follow-up).
* **Web client, Vijay:** to unblock W-12 and the browser part of W-10, add those scenarios to
  `capture_screenshots.py`.
