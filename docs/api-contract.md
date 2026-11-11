# API data contract — v1

Owner: API & platform workstream. Source of truth is the code in `app/contracts/`; the live JSON Schemas are
served at `GET /contract`, and the interactive OpenAPI docs at `GET /docs`.

The service is **decision support**: it proposes a classification. It never files a declaration; a licensed
customs broker decides.

## Headers

| Header | Direction | Meaning |
|---|---|---|
| `X-Correlation-ID` | request (optional) / response | 8–64 chars `[A-Za-z0-9._-]`. Generated if absent. Propagated to the queue, worker, every agent step and every log line. |
| `X-Client-ID` | request (optional) | Rate-limit key (1–64 chars). Defaults to the caller's IP. |
| `Location` | response to `POST /runs` | Status URL of the new run. |
| `Retry-After` | response to `429` | Seconds until the rate-limit window resets. |

## `POST /runs` — submit

Request body (`application/json`, max 16 KB). Unknown fields are rejected.

| Field | Type | Required | Rules |
|---|---|---|---|
| `contract_version` | `"1"` | no | Only `"1"` accepted. |
| `product_description` | string | **yes** | 10–2000 chars after trimming; ≥ 5 letters; no control characters. |
| `materials` | string[] | no | ≤ 10 items, each 2–50 chars, starts with a letter. Case-insensitive duplicates removed. |
| `chapter_hint` | `"73"` \| `"96"` | no | Advisory only. |
| `hs_code_hint` | string | no | 4, 6 or 8 digits. Advisory only. |
| `destination_country` | string | no | ISO 3166-1 alpha-2, upper case (`AE`). |
| `quantity` | integer | no | 1 – 1 000 000. |
| `quantity_unit` | `pcs` \| `kg` \| `set` \| `pair` \| `dozen` | no | |
| `client_reference` | string | no | ≤ 64 chars `[A-Za-z0-9._-]`. Echoed back, never sent to a model. |

Cross-field rules:

* `hs_code_hint` must start with `chapter_hint` when both are given.
* `quantity` and `quantity_unit` must be given together.

Guardrail: `product_description` and `materials` are screened for prompt injection before a run is created.

### Responses

**202 Accepted**

```json
{"run_id": "1f0c…", "status": "queued", "status_url": "/runs/1f0c…", "correlation_id": "…"}
```

**422 Rejected and quarantined.** No run is created and nothing is enqueued.

```json
{
  "error": "validation_failed",
  "quarantine_id": "9a7e…",
  "correlation_id": "…",
  "run_created": false,
  "errors": [
    {"field": "product_description", "message": "String should have at least 10 characters", "type": "string_too_short"},
    {"field": "hs_code_hint+chapter_hint", "message": "hs_code_hint 96170012 does not belong to chapter_hint 73", "type": "cross_field"}
  ]
}
```

Error `type` values include the Pydantic types (`missing`, `extra_forbidden`, `string_type`, `string_too_short`,
`literal_error`, `string_pattern_mismatch`, …) and our own: `cross_field`, `not_descriptive`,
`control_characters`, `invalid_material`, `json_invalid`, `content_type`, `too_large`,
`prompt_injection_suspected`.

**429 Rate limited**: `{"error": "rate_limited", "retry_after_s": 23, "limit_per_minute": 30}`

## `GET /runs/{run_id}` — poll

`run_id` is 32 lower-case hex characters. Returns `404` if unknown or expired (runs are kept 7 days by default).

| `status` | Meaning |
|---|---|
| `queued` | Accepted, waiting for a worker. |
| `running` | A worker is executing the agents. |
| `completed` | Routed to `clear-for-filing` or `seek-product-clarification`. |
| `escalated` | Routed to `specialist-classification-review`, either by the agents (ambiguous after GRI 3, out of scope) or by the platform (provider unavailable, loop budget exhausted, output policy). |
| `failed` | The run did not complete (internal error, or interrupted too often). **No result is returned.** |

```json
{
  "run_id": "…", "status": "completed", "correlation_id": "…", "trace_id": "…",
  "created_at": "…", "started_at": "…", "finished_at": "…", "updated_at": "…",
  "attempts": 1, "cache_hit": false, "client_reference": "PO-2026-0042",
  "result": {
    "route": "clear-for-filing",
    "basis": "Heading 9617 names vacuum flasks; GRI 1 applies before classification by material.",
    "hs_code": "96170012",
    "heading_title": "Vacuum flasks and other vacuum vessels, complete …",
    "gri_applied": ["GRI 1", "GRI 6"],
    "reasoning_summary": "…",
    "citations": [{"source_id": "cbic-ch96", "chunk_id": "ch96-0042", "locator": "Heading 9617", "quote": "…"}],
    "export_policy": {"policy": "Free", "conditions": null, "citations": […]},
    "verification": {"verified": true, "attempts": 1, "strategy_log": ["…"], "notes": null},
    "escalation_reason": null,
    "prompt_version": "classifier-v1",
    "policy_adjustments": [],
    "disclaimer": "Proposed classification for decision support only. …"
  },
  "error": null,
  "usage": {"steps": 6, "tool_calls": 5, "llm_calls": 4, "prompt_tokens": 5120, "completion_tokens": 610,
            "cost_usd": 0.0, "execution_seconds": 11.2, "end_to_end_seconds": 11.9},
  "events_url": "/runs/…/events"
}
```

The values above are placeholders and **not** the reference answer. The reference answer is agreed at Evaluation 1.

Guarantees enforced by the platform's output policy, whatever the agents return:

* `hs_code` is non-null only if `verification.verified` is true and at least one citation resolves.
* Every returned citation's `chunk_id` was retrieved during this run, and its `quote` occurs in that chunk.
* `route = clear-for-filing` requires a verified, cited code in chapter 73 or 96.
* Any intervention is listed in `policy_adjustments` and the route is downgraded to specialist review.

## `GET /runs/{run_id}/events` — reconstruct a run

Returns `{"run": <status response>, "events": [...]}`. Event `kind`s in order of appearance:

* `run_started`
* `step_start` / `step_end` (agent, step, outcome, latency_ms)
* `tool_call` (tool, query, outcome, latency_ms, passages[])
* `llm_call` (agent, step, prompt_version, provider, model, attempts, fallback_used, prompt_tokens, completion_tokens, cost_usd, latency_ms, messages, response)
* `output_policy`
* `budget_exceeded`
* `degraded`
* `cache_hit`
* `run_error`
* `run_finished` (status, route, usage)

Spans for the same run are in Jaeger under `trace_id`.

## Feedback (L6)

Owner: web client workstream. A reviewer rates a **finished** run; the entry is stamped with the run's `route`,
`hs_code` and `prompt_version`, so feedback can be compared across prompt versions and over time.

### `POST /runs/{run_id}/feedback`

| Field | Type | Required | Rules |
|---|---|---|---|
| `rating` | `"up"` \| `"down"` | **yes** | |
| `reasons` | string[] | no | Any of `wrong_hs_code`, `wrong_route`, `citation_problem`, `missing_notes`, `unclear_explanation`, `too_slow`, `other`. Duplicates removed. |
| `comment` | string | no | ≤ 1000 chars, no control characters. Stored for humans; **never sent to a model**. |
| `suggested_hs_code` | string | no | 4, 6 or 8 digits. |
| `reviewer` | string | no | ≤ 64 chars `[A-Za-z0-9._ -]`. |

Unknown fields are rejected. One entry per (run, `X-Client-ID`): rating the same run again replaces the earlier
entry, so a reviewer cannot double-count.

| Status | Body |
|---|---|
| `201` | `{"feedback_id": "…", "run_id": "…", "replaced_previous": false}` |
| `404` | `{"error": "not_found", "run_id": "…"}`: unknown or expired run. |
| `409` | `{"error": "run_not_finished", "run_id": "…", "status": "running"}` |
| `422` | `{"error": "validation_failed", "correlation_id": "…", "errors": [{"field", "message", "type"}]}`. Not quarantined: no run is involved. |

### `GET /runs/{run_id}/feedback`

`{"run_id": "…", "items": [<feedback record>, …]}`, oldest first.

### `GET /feedback/summary?recent=10`

```json
{
  "total": {"total": 12, "up": 9, "down": 3, "satisfaction": 0.75},
  "by_prompt_version": {"classifier-v1": {"total": 5, "up": 3, "down": 2, "satisfaction": 0.6}, "classifier-v2": {…}},
  "by_route": {"clear-for-filing": {…}, "specialist-classification-review": {…}},
  "by_day": {"2026-11-10": {…}, "2026-11-11": {…}},
  "reasons": {"missing_notes": 2, "wrong_hs_code": 1},
  "recent": [<feedback record>, …]
}
```

Feedback does not expire with the run. Prometheus counter: `ecs_feedback_total{rating, prompt_version}`.

## Operational endpoints

| Path | Purpose |
|---|---|
| `GET /health` | Liveness (process up). |
| `GET /ready` | Readiness (Redis reachable); `503` otherwise. The body also has `queue_depth` and `workers_alive` (agent-workers with a live heartbeat). `workers_alive: 0` does **not** make the API unready, because it can still accept and queue runs, but those runs will not progress until a worker starts (see `docs/incident-log.md`, INC-001). |
| `GET /metrics` | Prometheus exposition. |
| `GET /quarantine?limit=20` | Most recent rejected requests with their field errors and raw body (truncated to 4 KB). |
| `GET /contract` | JSON Schemas for request, result, run status and validation error. |
