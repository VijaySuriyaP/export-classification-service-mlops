"""Locust load test (D6): measures a *complete* run, submission to final result.

Each simulated user submits a run, then polls until it reaches a terminal state. The custom
``RUN complete_run`` entry in Locust's statistics is the SLO measurement; the ``POST /runs`` and
``GET /runs/[id]`` entries show API-only latency.

Run inside compose (LLM replaced by llm-stub):
    docker compose --profile loadtest up locust      # UI on http://localhost:8089
Headless example:
    docker compose --profile loadtest run --rm locust -f /mnt/locust/locustfile.py \
        --host http://api:8000 --headless -u 20 -r 2 -t 5m --csv /mnt/locust/results/run
"""

from __future__ import annotations

import os
import random
import time
import uuid

from locust import HttpUser, between, events, task

POLL_INTERVAL_S = float(os.getenv("LOADTEST_POLL_INTERVAL_S", "0.5"))
RUN_TIMEOUT_S = float(os.getenv("LOADTEST_RUN_TIMEOUT_S", "180"))
TERMINAL = {"completed", "escalated", "failed"}

DESCRIPTIONS = [
    "Stainless steel vacuum flask with a moulded plastic outer body.",
    "Stainless steel kitchen sink, single bowl, for domestic use.",
    "Ball-point pen with plastic barrel and replaceable refill.",
    "Iron or steel wood screws, threaded, zinc plated.",
    "Plastic hair comb, moulded, for personal use.",
    "Cast iron cooking pot with enamel coating.",
]


class ClassificationUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        # each simulated user is its own client for rate limiting
        self.client.headers.update({"X-Client-ID": f"locust-{uuid.uuid4().hex[:10]}"})

    @task
    def complete_run(self) -> None:
        started = time.perf_counter()
        body = {"product_description": random.choice(DESCRIPTIONS)}
        with self.client.post("/runs", json=body, name="POST /runs", catch_response=True) as resp:
            if resp.status_code != 202:
                resp.failure(f"submit {resp.status_code}")
                return
            run_id = resp.json()["run_id"]
        status, exc = None, None
        while time.perf_counter() - started < RUN_TIMEOUT_S:
            time.sleep(POLL_INTERVAL_S)
            poll = self.client.get(f"/runs/{run_id}", name="GET /runs/[id]")
            if poll.status_code == 200:
                status = poll.json()["status"]
                if status in TERMINAL:
                    break
        else:
            exc = TimeoutError(f"run not finished after {RUN_TIMEOUT_S}s")
        if status == "failed":
            exc = RuntimeError("run failed")
        events.request.fire(
            request_type="RUN", name="complete_run",
            response_time=(time.perf_counter() - started) * 1000, response_length=0,
            exception=exc, context={},
        )
