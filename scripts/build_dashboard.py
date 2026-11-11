"""Generate observability/grafana/dashboards/ecs-overview.json.

    python scripts/build_dashboard.py

Edit the PANELS list rather than the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "observability" / "grafana" / "dashboards" / "ecs-overview.json"
DS = {"type": "prometheus", "uid": "prometheus"}

# (row title, [(panel title, unit, [(expr, legend)], kind)])
PANELS = [
    ("Service level (complete run, submission to result)", [
        ("Run latency p50 / p95 (SLO metric)", "s", [
            ("histogram_quantile(0.50, sum(rate(ecs_run_end_to_end_seconds_bucket[5m])) by (le))", "p50"),
            ("histogram_quantile(0.95, sum(rate(ecs_run_end_to_end_seconds_bucket[5m])) by (le))", "p95"),
        ], "timeseries"),
        ("Throughput (runs finished / s)", "reqps", [
            ("sum(rate(ecs_runs_finished_total[1m])) by (status)", "{{status}}"),
        ], "timeseries"),
        ("Run outcomes by route", "short", [
            ("sum(increase(ecs_runs_finished_total[15m])) by (route)", "{{route}}"),
        ], "timeseries"),
        ("Error rate (failed runs + HTTP 5xx)", "percentunit", [
            ("sum(rate(ecs_runs_finished_total{status='failed'}[5m])) / clamp_min(sum(rate(ecs_runs_finished_total[5m])), 1e-9)", "failed runs"),
            ("sum(rate(ecs_http_requests_total{status=~'5..'}[5m])) / clamp_min(sum(rate(ecs_http_requests_total[5m])), 1e-9)", "HTTP 5xx"),
        ], "timeseries"),
    ]),
    ("API & ingestion", [
        ("API latency p95 by route", "s", [
            ("histogram_quantile(0.95, sum(rate(ecs_http_request_duration_seconds_bucket[5m])) by (le, route))", "{{route}}"),
        ], "timeseries"),
        ("Submitted / rejected / rate-limited", "short", [
            ("sum(rate(ecs_runs_submitted_total[1m]))*60", "submitted /min"),
            ("sum(rate(ecs_requests_rejected_total[1m])) by (reason)*60", "rejected {{reason}} /min"),
            ("sum(rate(ecs_rate_limited_total[1m]))*60", "rate-limited /min"),
        ], "timeseries"),
        ("Queue depth", "short", [("max(ecs_queue_depth)", "queued")], "timeseries"),
        ("Queue wait p95", "s", [
            ("histogram_quantile(0.95, sum(rate(ecs_run_queue_wait_seconds_bucket[5m])) by (le))", "p95 wait"),
        ], "timeseries"),
    ]),
    ("Agents (A6)", [
        ("Task success rate per agent", "percentunit", [
            ("sum(rate(ecs_agent_steps_total{outcome='success'}[5m])) by (agent) / clamp_min(sum(rate(ecs_agent_steps_total[5m])) by (agent), 1e-9)", "{{agent}}"),
        ], "timeseries"),
        ("Step latency p95 per agent step", "s", [
            ("histogram_quantile(0.95, sum(rate(ecs_agent_step_duration_seconds_bucket[5m])) by (le, agent, step))", "{{agent}}.{{step}}"),
        ], "timeseries"),
        ("Steps and tool calls per run (avg)", "short", [
            ("sum(rate(ecs_run_steps_sum[5m])) / clamp_min(sum(rate(ecs_run_steps_count[5m])), 1e-9)", "steps/run"),
            ("sum(rate(ecs_run_tool_calls_sum[5m])) / clamp_min(sum(rate(ecs_run_tool_calls_count[5m])), 1e-9)", "tool calls/run"),
        ], "timeseries"),
        ("Tool calls by outcome", "short", [
            ("sum(rate(ecs_tool_calls_total[5m])) by (tool, outcome)*60", "{{tool}} {{outcome}} /min"),
        ], "timeseries"),
        ("Output policy interventions", "short", [
            ("sum(increase(ecs_output_policy_adjustments_total[15m])) by (rule)", "{{rule}}"),
        ], "timeseries"),
        ("Run cache hit ratio", "percentunit", [
            ("sum(rate(ecs_run_cache_total{result='hit'}[5m])) / clamp_min(sum(rate(ecs_run_cache_total[5m])), 1e-9)", "hit ratio"),
        ], "timeseries"),
    ]),
    ("Model provider (A5, L5)", [
        ("Circuit breaker state (0 closed, 1 half-open, 2 open)", "short", [
            ("max(ecs_circuit_breaker_state) by (provider)", "{{provider}}"),
        ], "timeseries"),
        ("Model call retry rate", "percentunit", [
            ("sum(rate(ecs_llm_retries_total[5m])) / clamp_min(sum(rate(ecs_llm_calls_total[5m])), 1e-9)", "retries / call"),
        ], "timeseries"),
        ("Model calls by outcome", "short", [
            ("sum(rate(ecs_llm_calls_total[1m])) by (provider, outcome)*60", "{{provider}} {{outcome}} /min"),
            ("sum(rate(ecs_circuit_breaker_rejections_total[1m])) by (provider)*60", "{{provider}} refused by breaker /min"),
            ("sum(rate(ecs_llm_fallback_total[1m]))*60", "served by fallback /min"),
        ], "timeseries"),
        ("Model latency p95", "s", [
            ("histogram_quantile(0.95, sum(rate(ecs_llm_call_duration_seconds_bucket[5m])) by (le, provider))", "{{provider}}"),
        ], "timeseries"),
        ("Tokens per minute by agent", "short", [
            ("sum(rate(ecs_llm_tokens_total[5m])) by (agent, kind)*60", "{{agent}} {{kind}}"),
        ], "timeseries"),
        ("Cost per 100 runs (USD)", "currencyUSD", [
            ("100 * sum(increase(ecs_llm_cost_usd_total[1h])) / clamp_min(sum(increase(ecs_runs_finished_total[1h])), 1)", "per 100 runs"),
        ], "timeseries"),
    ]),
    ("Containers (availability, utilisation, uptime)", [
        ("Availability (scrape up)", "short", [("up", "{{job}} {{instance}}")], "timeseries"),
        ("CPU per container (cores)", "short", [
            ("rate(process_cpu_seconds_total[1m])", "{{job}} {{instance}}"),
        ], "timeseries"),
        ("Memory per container (RSS)", "bytes", [
            ("process_resident_memory_bytes", "{{job}} {{instance}}"),
        ], "timeseries"),
        ("Container uptime", "s", [
            ("time() - process_start_time_seconds", "{{job}} {{instance}}"),
        ], "timeseries"),
        ("Busy workers", "short", [("sum(ecs_worker_busy)", "busy"), ("count(up{job='agent-worker'}==1)", "up")], "timeseries"),
    ]),
]


def build() -> dict:
    panels, pid, y = [], 1, 0
    for row_title, row_panels in PANELS:
        panels.append({"type": "row", "title": row_title, "id": pid, "collapsed": False,
                       "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
        pid += 1
        y += 1
        for i, (title, unit, targets, kind) in enumerate(row_panels):
            panels.append({
                "type": kind, "title": title, "id": pid, "datasource": DS,
                "gridPos": {"h": 8, "w": 8, "x": (i % 3) * 8, "y": y + (i // 3) * 8},
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
                "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
                "targets": [{"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(65 + j)}
                            for j, (expr, legend) in enumerate(targets)],
            })
            pid += 1
        y += ((len(row_panels) + 2) // 3) * 8
    return {
        "uid": "ecs-overview", "title": "Export Classification - Overview", "schemaVersion": 39, "version": 1,
        "editable": True, "refresh": "10s", "time": {"from": "now-30m", "to": "now"},
        "tags": ["ecs"], "templating": {"list": []}, "annotations": {"list": []}, "panels": panels,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
