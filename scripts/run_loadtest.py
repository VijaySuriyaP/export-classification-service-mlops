#!/usr/bin/env python3
"""Run the D6 / A7 load-test matrix against the running Compose stack and write the report.

    docker compose up -d --build --wait
    python scripts/run_loadtest.py --workers 1,3 --users 5,10,20,40 --duration 180

For every (workers, users) pair it scales ``agent-worker``, waits for the queue to drain, runs Locust
headless against the stack (the LLM is the stub service, so this measures our system, not a
provider's rate limits), then asks Prometheus for server-side figures over the same window (queue
wait, worker busy ratio, API latency) so the report can name the bottleneck. Finally it calls
scripts/loadtest_report.py.

Per the brief the SLO must be declared BEFORE measuring: this script refuses to run while
loadtest/slo.json has status "PROPOSED". Edit it to "DECLARED" (with date and author) once the team
agrees, commit that, then measure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "loadtest" / "results"


def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=check)


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def prom_scalar(base: str, query: str) -> float | None:
    data = get_json(f"{base}/api/v1/query?" + urllib.parse.urlencode({"query": query}))
    result = data["data"]["result"]
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if value != value else value  # NaN -> None


def collect_prometheus(base: str, window_s: int) -> dict:
    w = f"{window_s}s"
    return {
        "window_s": window_s,
        "queue_wait_p95_s": prom_scalar(base, f"histogram_quantile(0.95, sum(increase(ecs_run_queue_wait_seconds_bucket[{w}])) by (le))"),
        "worker_busy_ratio": prom_scalar(base, f"avg(avg_over_time(ecs_worker_busy[{w}]))"),
        "api_post_p95_s": prom_scalar(base, "histogram_quantile(0.95, sum(increase("
                                            f"ecs_http_request_duration_seconds_bucket{{method='POST',route='/runs'}}[{w}])) by (le))"),
        "run_p95_s": prom_scalar(base, f"histogram_quantile(0.95, sum(increase(ecs_run_end_to_end_seconds_bucket[{w}])) by (le))"),
    }


def wait_queue_drained(api: str, timeout_s: int = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if get_json(f"{api}/ready").get("queue_depth", 1) == 0:
            time.sleep(3)
            return
        time.sleep(2)
    print("warning: queue did not drain; results of this run may be contaminated", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workers", default="1,3", help="agent-worker replica counts, e.g. 1,3")
    p.add_argument("--users", default="5,10,20,40", help="concurrent simulated users per step")
    p.add_argument("--duration", type=int, default=180, help="seconds per step (after ramp-up)")
    p.add_argument("--spawn-rate", type=float, default=0, help="users/s; default users/5")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--prometheus", default="http://localhost:9090")
    p.add_argument("--slo", type=Path, default=ROOT / "loadtest" / "slo.json")
    p.add_argument("--allow-undeclared", action="store_true",
                   help="dry run only: measure although the SLO is still PROPOSED (results are not valid evidence)")
    args = p.parse_args()

    slo = json.loads(args.slo.read_text(encoding="utf-8"))
    if slo["status"] != "DECLARED" and not args.allow_undeclared:
        print(f"Refusing to measure: SLO status is {slo['status']!r}. Declare it first (see loadtest/slo.json and "
              "docs/slo.md); a target set after seeing results does not satisfy D6.", file=sys.stderr)
        return 2

    RESULTS.mkdir(parents=True, exist_ok=True)
    RESULTS.chmod(0o777)  # the Locust container writes here as another user
    for workers in (int(w) for w in args.workers.split(",")):
        sh(["docker", "compose", "up", "-d", "--scale", f"agent-worker={workers}", "--no-recreate",
            "--wait", "--wait-timeout", "180"])
        time.sleep(10)  # let Prometheus discover the replicas
        for users in (int(u) for u in args.users.split(",")):
            wait_queue_drained(args.api)
            prefix = f"w{workers}_u{users}"
            rate = args.spawn_rate or max(1.0, users / 5)
            ramp = int(users / rate) + 1
            sh(["docker", "compose", "--profile", "loadtest", "run", "--rm", "-T", "locust",
                "-f", "/mnt/locust/locustfile.py", "--host", "http://api:8000", "--headless",
                "-u", str(users), "-r", str(rate), "-t", f"{args.duration + ramp}s", "--reset-stats",
                "--csv", f"/mnt/locust/results/{prefix}", "--only-summary"], check=False)
            time.sleep(10)  # one more scrape so the window is covered
            prom = collect_prometheus(args.prometheus, args.duration + 10)
            (RESULTS / f"{prefix}_prom.json").write_text(json.dumps(prom, indent=2), encoding="utf-8")
            print(f"{prefix}: {prom}", flush=True)

    return sh([sys.executable, "scripts/loadtest_report.py", str(RESULTS), "--slo", str(args.slo),
               "--out", str(RESULTS / "report.md")], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
