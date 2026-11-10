#!/usr/bin/env python3
"""Turn Locust CSV output into the D6 / A7 report: RPS, p50, p95 against the declared SLO, the
concurrency at which the target first fails, the component responsible, and the effect of scaling.

    python scripts/loadtest_report.py loadtest/results --slo loadtest/slo.json --out loadtest/results/report.md

Input: one ``w{workers}_u{users}_stats.csv`` per run (written by scripts/run_loadtest.py, or by hand
with ``locust --csv w3_u20``), optionally with a ``w{workers}_u{users}_prom.json`` sidecar holding
server-side measurements used to name the bottleneck. The measurement of a *complete run* is the
custom ``complete_run`` row from loadtest/locustfile.py (submission to terminal status).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RUN_ROW = "complete_run"
SUBMIT_ROW = "POST /runs"
NAME_RE = re.compile(r"^w(?P<workers>\d+)_u(?P<users>\d+)_stats\.csv$")


@dataclass
class Result:
    workers: int
    users: int
    requests: int
    failures: int
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    submit_p95_ms: float | None = None
    prom: dict = field(default_factory=dict)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.requests if self.requests else 1.0


def _num(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "", "N/A") else float("nan")
    except ValueError:
        return float("nan")


def load_result(csv_path: Path) -> Result:
    match = NAME_RE.match(csv_path.name)
    if not match:
        raise ValueError(f"unexpected file name {csv_path.name}")
    rows = {r["Name"]: r for r in csv.DictReader(csv_path.open(encoding="utf-8", newline=""))}
    if RUN_ROW not in rows:
        raise ValueError(f"{csv_path.name} has no '{RUN_ROW}' row; was it produced with loadtest/locustfile.py?")
    run = rows[RUN_ROW]
    submit = rows.get(SUBMIT_ROW)
    prom_path = csv_path.with_name(csv_path.name.replace("_stats.csv", "_prom.json"))
    return Result(
        workers=int(match["workers"]), users=int(match["users"]),
        requests=int(_num(run["Request Count"])), failures=int(_num(run["Failure Count"])),
        rps=_num(run["Requests/s"]), p50_ms=_num(run["50%"]), p95_ms=_num(run["95%"]), p99_ms=_num(run["99%"]),
        submit_p95_ms=_num(submit["95%"]) if submit else None,
        prom=json.loads(prom_path.read_text(encoding="utf-8")) if prom_path.exists() else {},
    )


def load_results(directory: Path) -> list[Result]:
    files = sorted(p for p in directory.glob("w*_u*_stats.csv") if NAME_RE.match(p.name))
    return sorted((load_result(p) for p in files), key=lambda r: (r.workers, r.users))


def evaluate(result: Result, slo: dict) -> dict[str, bool]:
    checks = {
        "p95": result.p95_ms <= slo["complete_run_p95_ms"],
        "failures": result.failure_rate <= slo["max_failure_rate"],
        "submit": result.submit_p95_ms is None or result.submit_p95_ms <= slo["submit_p95_ms"],
    }
    checks["met"] = all(checks.values())
    return checks


def infer_bottleneck(run_p95_s: float, queue_wait_p95_s: float | None, worker_busy: float | None,
                     api_p95_s: float | None) -> str:
    """Name the component that explains the latency, from server-side measurements."""
    if queue_wait_p95_s is not None and worker_busy is not None:
        if queue_wait_p95_s >= 0.3 * run_p95_s and worker_busy >= 0.8:
            return "agent-worker capacity (runs wait in the queue while workers are busy)"
    if api_p95_s is not None and api_p95_s > 0.5:
        return "API (submission latency is high)"
    if queue_wait_p95_s is not None and queue_wait_p95_s < 0.1 * run_p95_s:
        return "run execution time (model/stub latency and agent steps), not queueing"
    return "inconclusive (see queue wait and worker busy ratio)"


def bottleneck_of(result: Result) -> str:
    p = result.prom
    if not p:
        return "no server-side data"
    return infer_bottleneck(result.p95_ms / 1000, p.get("queue_wait_p95_s"), p.get("worker_busy_ratio"),
                            p.get("api_post_p95_s"))


def first_failure(results: list[Result], slo: dict) -> Result | None:
    return next((r for r in sorted(results, key=lambda r: r.users) if not evaluate(r, slo)["met"]), None)


def _fmt(value, spec=".1f", unit="") -> str:
    return "n/a" if value is None or value != value else f"{value:{spec}}{unit}"


def render_markdown(results: list[Result], slo: dict) -> str:
    out = [
        "# Load test report",
        "",
        f"SLO ({slo['status']}"
        + (f", declared {slo['declared_at']} by {slo['declared_by']}" if slo.get("declared_at") else "")
        + f"): complete-run p95 <= {slo['complete_run_p95_ms'] / 1000:g} s, POST /runs p95 <= "
        f"{slo['submit_p95_ms']} ms, failure rate <= {slo['max_failure_rate']:.0%}.",
        "A *complete run* is measured from `POST /runs` until the run reaches a terminal status.",
        "",
        "| Workers | Users | Runs | Failed | RPS | p50 (s) | p95 (s) | p99 (s) | POST p95 (ms) | SLO | Bottleneck |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---|",
    ]
    for r in results:
        verdict = evaluate(r, slo)
        out.append(f"| {r.workers} | {r.users} | {r.requests} | {r.failures} | {_fmt(r.rps, '.2f')} | "
                   f"{_fmt(r.p50_ms / 1000)} | {_fmt(r.p95_ms / 1000)} | {_fmt(r.p99_ms / 1000)} | "
                   f"{_fmt(r.submit_p95_ms, '.0f')} | {'met' if verdict['met'] else 'MISSED'} | {bottleneck_of(r)} |")

    out += ["", "## Concurrency at which the target fails", ""]
    for workers in sorted({r.workers for r in results}):
        group = [r for r in results if r.workers == workers]
        failed = first_failure(group, slo)
        if failed:
            out.append(f"- **{workers} worker(s):** first missed at **{failed.users} concurrent users** "
                       f"(p95 {_fmt(failed.p95_ms / 1000)} s, {failed.failure_rate:.1%} failed). "
                       f"Responsible: {bottleneck_of(failed)}.")
        else:
            out.append(f"- **{workers} worker(s):** target met at every tested level "
                       f"(up to {max(r.users for r in group)} users); the failure point was not reached, "
                       "so test higher concurrency before claiming a limit.")

    worker_counts = sorted({r.workers for r in results})
    if len(worker_counts) > 1:
        base_w, top_w = worker_counts[0], worker_counts[-1]
        out += ["", f"## Effect of scaling ({base_w} -> {top_w} workers)", "",
                "| Users | RPS before | RPS after | Throughput x | p95 before (s) | p95 after (s) |",
                "|---:|---:|---:|---:|---:|---:|"]
        before = {r.users: r for r in results if r.workers == base_w}
        after = {r.users: r for r in results if r.workers == top_w}
        for users in sorted(before.keys() & after.keys()):
            b, a = before[users], after[users]
            ratio = a.rps / b.rps if b.rps else float("nan")
            out.append(f"| {users} | {_fmt(b.rps, '.2f')} | {_fmt(a.rps, '.2f')} | {_fmt(ratio, '.2f')} | "
                       f"{_fmt(b.p95_ms / 1000)} | {_fmt(a.p95_ms / 1000)} |")
        out += ["", "If throughput does not rise roughly with the worker count, the table above names the component "
                    "that is limiting instead (queue wait low and API p95 high means the API; queue wait low and "
                    "workers idle means the load generator or the stub)."]
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results_dir", type=Path)
    p.add_argument("--slo", type=Path, default=Path("loadtest/slo.json"))
    p.add_argument("--out", type=Path, help="also write the Markdown report here")
    args = p.parse_args()
    results = load_results(args.results_dir)
    if not results:
        print(f"no w*_u*_stats.csv files in {args.results_dir}", file=sys.stderr)
        return 2
    report = render_markdown(results, json.loads(args.slo.read_text(encoding="utf-8")))
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
