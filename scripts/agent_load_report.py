"""Combine isolated real-agent performance cases, retaining separate cached/uncached results."""

import json
import re
import sys
from pathlib import Path

from loadtest_report import load_result, render_markdown

CASE = re.compile(r"agents-w(\d+)-u(\d+)-cache(\d+)$")


def build_report(root):
    cases = {}
    for folder in root.iterdir():
        match = CASE.fullmatch(folder.name)
        if not match or not folder.is_dir():
            continue
        workers, users, ttl = map(int, match.groups())
        cases[workers, users, ttl] = load_result(folder / f"w{workers}_u{users}_stats.csv")
    expected = {(w, u, 0) for w in (1, 3) for u in (5, 20, 80)} | {(3, 20, 3600)}
    if set(cases) != expected:
        raise ValueError(f"Incomplete matrix: missing {expected - set(cases)}; extra {set(cases) - expected}")
    slo = json.loads((root / "agents-w1-u5-cache0" / "slo.json").read_text())
    uncached = [r for (w, u, ttl), r in sorted(cases.items()) if ttl == 0]
    text = (
        "# Real-agent platform performance (scripted model responses)\n\n"
        "**Not classification-correctness evidence.** Actual LangGraph classifier/verifier, real FAISS "
        "retrieval and platform; canned model responses approve a performance fixture. Two 1.5-second "
        "model calls per uncached run. Follow-up/research paths and real-provider rate limits are not measured. "
        "Identical inputs are repeated. Cache is prewarmed by a preflight run; this is best-case caching, "
        "not a production hit-rate forecast. Hosted hardware differs across isolated jobs. "
        "Measurement lasts five minutes after ramp-up plus a completion tail.\n\n"
        + render_markdown(uncached, slo)
        + "\n## Cache comparison (3 workers, 20 users)\n\n"
        "| Cache | p95 (s) | RPS | Server runs | Model calls/run | Estimated tokens/run | Cache hit ratio | Actual USD/100 runs |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    counts = {}
    for ttl in (0, 3600):
        result = cases[3, 20, ttl]
        a = result.prom.get("accounting")
        if not a or a["runs"] <= 0:
            raise ValueError("Missing server accounting; cannot report cache/cost effect")
        counts[ttl] = a
        tokens = a["prompt_tokens"] + a["completion_tokens"]
        lookups = a["cache_hits"] + a["cache_misses"]
        hit = a["cache_hits"] / lookups if lookups else 0
        text += (
            f"| {'off' if ttl == 0 else 'on (warm)'} | {result.p95_ms/1000:.2f} | {result.rps:.2f} "
            f"| {a['runs']:.0f} | {a['model_calls']/a['runs']:.3f} | {tokens/a['runs']:.1f} "
            f"| {hit:.1%} | {100*a['cost_usd']/a['runs']:.6f} |\n"
        )
    before, after = cases[3, 20, 0], cases[3, 20, 3600]
    reduction = 100 * (1 - after.p95_ms / before.p95_ms)
    off, on = counts[0], counts[3600]
    avoided = off["model_calls"]/off["runs"] - on["model_calls"]/on["runs"]
    driver = max(("classifier", "verifier"), key=lambda a: off[a + "_tokens"])
    text += (
        f"\nWarm-cache p95 reduction: **{reduction:.1f}%**. "
        f"Avoided model calls per completed server run: **{avoided:.3f}**.\n\n"
        f"The largest uncached token contributor was **{driver}**. Stub tokens are estimates "
        "from character counts, not provider tokenizer measurements. Actual provider spend is **zero** "
        "in both cases; no dollar savings or Cohere cost is claimed. For a priced provider, estimated "
        "USD/run = prompt_tokens/run × prompt_USD_per_1K / 1000 + completion_tokens/run × "
        "completion_USD_per_1K / 1000, using configured prices at call time.\n\n"
        "Accounting uses server counter deltas and includes runs finishing in the completion tail; "
        "Locust rows reflect completed client attempts and may differ. Preflight accounting is excluded. "
        "Prometheus busy ratio is worker-scoped; its sampling window is after shutdown and can include "
        "drain/idle time. Bottleneck inference is heuristic. No service-availability percentage or "
        "peak-container-resource claim is made from snapshots. Raw sidecars, conditions, preflight "
        "trace/events and CSVs are kept alongside this report.\n"
    )
    return text


if __name__ == "__main__":
    root = Path(sys.argv[1])
    text = build_report(root)
    (root / "report.md").write_text(text, encoding="utf-8")
    print(text)
