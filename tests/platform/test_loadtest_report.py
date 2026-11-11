"""The load-test report decides pass/fail against the SLO, so its logic must be right."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                      # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


report = _module("loadtest_report")

HEADER = ('"Type","Name","Request Count","Failure Count","Median Response Time","Average Response Time",'
          '"Min Response Time","Max Response Time","Average Content Size","Requests/s","Failures/s",'
          '"50%","66%","75%","80%","90%","95%","98%","99%","99.9%","99.99%","100%"')

SLO = {"status": "DECLARED", "declared_at": "2026-11-12", "declared_by": "team", "complete_run_p95_ms": 60000,
       "submit_p95_ms": 300, "max_failure_rate": 0.01}


def _row(rtype, name, count, failures, rps, p50, p95, p99):
    return (f'"{rtype}","{name}",{count},{failures},{p50},{p50},1,{p99},0,{rps},0,'
            f'{p50},{p50},{p50},{p50},{p95},{p95},{p95},{p99},{p99},{p99},{p99}')


def _write(directory: Path, workers, users, *, count=100, failures=0, rps=2.0, p50=8000, p95=20000, p99=25000,
           submit_p95=40, prom=None):
    lines = [HEADER, _row("RUN", "complete_run", count, failures, rps, p50, p95, p99),
             _row("POST", "POST /runs", count, 0, rps, 20, submit_p95, 80),
             _row("GET", "GET /runs/[id]", count * 10, 0, rps * 10, 5, 30, 60)]
    (directory / f"w{workers}_u{users}_stats.csv").write_text("\n".join(lines), encoding="utf-8")
    if prom is not None:
        (directory / f"w{workers}_u{users}_prom.json").write_text(json.dumps(prom), encoding="utf-8")


def test_parses_the_complete_run_row(tmp_path):
    _write(tmp_path, 1, 10, count=120, failures=3, rps=1.5, p50=9000, p95=31000, p99=44000, submit_p95=55)
    (result,) = report.load_results(tmp_path)
    assert (result.workers, result.users, result.requests, result.failures) == (1, 10, 120, 3)
    assert (result.rps, result.p50_ms, result.p95_ms, result.p99_ms, result.submit_p95_ms) == (1.5, 9000, 31000, 44000, 55)
    assert result.failure_rate == pytest.approx(0.025)


def test_results_sorted_and_foreign_files_ignored(tmp_path):
    _write(tmp_path, 3, 20)
    _write(tmp_path, 1, 40)
    _write(tmp_path, 1, 5)
    (tmp_path / "notes_stats.csv").write_text("ignored")
    assert [(r.workers, r.users) for r in report.load_results(tmp_path)] == [(1, 5), (1, 40), (3, 20)]


def test_csv_without_run_row_is_rejected(tmp_path):
    (tmp_path / "w1_u5_stats.csv").write_text(HEADER + "\n" + _row("POST", "POST /runs", 5, 0, 1, 10, 20, 30))
    with pytest.raises(ValueError, match="complete_run"):
        report.load_results(tmp_path)


@pytest.mark.parametrize("kwargs, met", [
    ({"p95": 59_999}, True), ({"p95": 60_000}, True), ({"p95": 60_001}, False),     # the boundary is inclusive
    ({"failures": 1, "count": 100}, True), ({"failures": 2, "count": 100}, False),   # 1% allowed, 2% not
    ({"submit_p95": 301}, False),
])
def test_slo_verdict(tmp_path, kwargs, met):
    _write(tmp_path, 1, 10, **kwargs)
    (result,) = report.load_results(tmp_path)
    assert report.evaluate(result, SLO)["met"] is met


def test_first_failure_is_the_lowest_failing_concurrency(tmp_path):
    for users, p95 in ((5, 12_000), (10, 30_000), (20, 70_000), (40, 150_000)):
        _write(tmp_path, 1, users, p95=p95)
    results = report.load_results(tmp_path)
    assert report.first_failure(results, SLO).users == 20
    assert report.first_failure(results[:2], SLO) is None


@pytest.mark.parametrize("queue, busy, api, expected", [
    (30, 0.95, 0.05, "worker capacity"),          # waiting in the queue while workers are saturated
    (1, 0.30, 1.2, "API"),
    (2, 0.40, 0.05, "run execution time"),         # no queueing: model/agent latency dominates
    (20, 0.30, 0.05, "inconclusive"),             # queueing but idle workers: not explained by our data
])
def test_bottleneck_inference(queue, busy, api, expected):
    assert expected in report.infer_bottleneck(run_p95_s=60, queue_wait_p95_s=queue, worker_busy=busy, api_p95_s=api)


def test_markdown_report_names_failure_point_and_scaling_effect(tmp_path):
    saturated = {"queue_wait_p95_s": 40, "worker_busy_ratio": 0.97, "api_post_p95_s": 0.05}
    healthy = {"queue_wait_p95_s": 1, "worker_busy_ratio": 0.4, "api_post_p95_s": 0.05}
    _write(tmp_path, 1, 10, rps=1.0, p95=30_000, prom=healthy)
    _write(tmp_path, 1, 20, rps=1.1, p95=90_000, prom=saturated)
    _write(tmp_path, 3, 10, rps=1.0, p95=20_000, prom=healthy)
    _write(tmp_path, 3, 20, rps=2.9, p95=35_000, prom=healthy)
    text = report.render_markdown(report.load_results(tmp_path), SLO)
    assert "DECLARED, declared 2026-11-12 by team" in text and "<= 60 s" in text
    assert "**1 worker(s):** first missed at **20 concurrent users**" in text
    assert "agent-worker capacity" in text
    assert "**3 worker(s):** target met at every tested level" in text
    assert "Effect of scaling (1 -> 3 workers)" in text and "| 20 | 1.10 | 2.90 | 2.64 |" in text


def test_cli_writes_report_file(tmp_path):
    _write(tmp_path, 1, 5)
    slo = tmp_path / "slo.json"
    slo.write_text(json.dumps(SLO))
    out = tmp_path / "report.md"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/loadtest_report.py"), str(tmp_path), "--slo", str(slo),
                           "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0 and "Load test report" in out.read_text(encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert subprocess.run([sys.executable, str(ROOT / "scripts/loadtest_report.py"), str(empty), "--slo", str(slo)],
                          capture_output=True).returncode == 2


def test_runner_refuses_to_measure_before_slo_is_declared(tmp_path):
    slo = tmp_path / "slo.json"
    slo.write_text(json.dumps({**SLO, "status": "PROPOSED"}))
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/run_loadtest.py"), "--slo", str(slo)],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "Refusing to measure" in proc.stderr


def test_committed_slo_file_is_still_only_proposed():
    """Guards the D6 rule: nobody flips this to DECLARED without a date and an author."""
    slo = json.loads((ROOT / "loadtest/slo.json").read_text(encoding="utf-8"))
    if slo["status"] == "DECLARED":
        assert slo["declared_at"] and slo["declared_by"], "a DECLARED SLO needs declared_at and declared_by"
    else:
        assert slo["status"] == "PROPOSED"
