"""Assert the remote test uses the real two-agent runner before measuring it."""

import json
import time
import urllib.request

API = "http://localhost:8000"


def get(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.load(r)


def check():
    payload = json.dumps({
        "product_description": "Stainless steel vacuum flask with a moulded plastic outer body.",
    }).encode()
    req = urllib.request.Request(API + "/runs", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        sub = json.load(r)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = get("/runs/" + sub["run_id"])
        if run["status"] in {"completed", "escalated", "failed"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Preflight run did not finish")
    assert run["status"] == "completed", run
    assert not run["cache_hit"], "Preflight must exercise the actual agent path"
    assert run["usage"]["llm_calls"] == 2, run["usage"]
    assert run["usage"]["tool_calls"] >= 1, run["usage"]
    events = get("/runs/" + sub["run_id"] + "/events")["events"]
    assert {e.get("agent") for e in events if e["kind"] == "llm_call"} == {"classifier", "verifier"}
    assert any(e["kind"] == "handoff" for e in events)
    assert any(e["kind"] == "verdict" for e in events)
    return {"run": run, "events": events,
            "scope": "Performance fixture; scripted approvals do not demonstrate classification accuracy."}


if __name__ == "__main__":
    from pathlib import Path

    result = check()
    Path("loadtest/results").mkdir(parents=True, exist_ok=True)
    Path("loadtest/results/preflight.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("PASS: actual classifier + verifier, real retrieval, two stub model calls, handoff and verdict.")
