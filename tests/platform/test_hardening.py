"""Redis outage behaviour (A5, F8): fail cleanly in the API, survive and recover in the worker."""

import json

import pytest

from app.agents.stub_runner import StubAgentRunner
from app.contracts import ClassificationResult, Verification
from app.worker.worker import Worker

from .conftest import VACUUM_FLASK


# ---------------------------------------------------------------- API
def test_api_returns_503_not_500_when_redis_is_down(client, redis_server):
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    redis_server.connected = False

    submit = client.post("/runs", json={"product_description": VACUUM_FLASK})
    assert submit.status_code == 503
    assert submit.headers["Retry-After"] == "5"
    assert submit.json()["error"] == "service_unavailable"
    assert "X-Correlation-ID" in submit.headers

    assert client.get(f"/runs/{run_id}").status_code == 503
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200          # liveness does not depend on Redis
    assert "ecs_redis_errors_total" in client.get("/metrics").text


def test_api_recovers_when_redis_returns(client, redis_server):
    redis_server.connected = False
    assert client.post("/runs", json={"product_description": VACUUM_FLASK}).status_code == 503
    redis_server.connected = True
    assert client.post("/runs", json={"product_description": VACUUM_FLASK}).status_code == 202


def test_no_partial_run_when_redis_fails_during_submit(client, redis_server, redis_client):
    redis_server.connected = False
    client.post("/runs", json={"product_description": VACUUM_FLASK})
    redis_server.connected = True
    assert not list(redis_client.scan_iter("ecs:run:*"))
    assert redis_client.llen("ecs:queue:runs") == 0


# ---------------------------------------------------------------- worker
def _worker(settings, store, runner, sleeps=None, on_sleep=None):
    worker = Worker(settings, store, runner, None, worker_id="w-test")
    worker.poll_timeout_s = 0.05

    def fake_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)
        if on_sleep:
            on_sleep()

    worker._sleep = fake_sleep
    return worker


class _Ok:
    prompt_version = "test-v1"

    def run(self, request, recorder):
        with recorder.step("classifier", "noop"):
            pass
        return ClassificationResult(route="specialist-classification-review", basis="test",
                                    verification=Verification(verified=False), prompt_version="test-v1")


def test_worker_survives_redis_outage_with_growing_backoff(settings, store, redis_server):
    sleeps = []
    worker = _worker(settings, store, _Ok(), sleeps)
    redis_server.connected = False
    for _ in range(5):
        worker.loop_iteration()            # must not raise
    assert sleeps == [0.5, 1.0, 2.0, 4.0, 8.0]
    redis_server.connected = True
    worker.loop_iteration()
    assert worker._redis_backoff == 0.0     # reset after a healthy pass


def test_backoff_is_capped(settings, store, redis_server):
    sleeps = []
    worker = _worker(settings, store, _Ok(), sleeps)
    redis_server.connected = False
    for _ in range(8):
        worker.loop_iteration()
    assert max(sleeps) == 10.0


def test_in_flight_run_is_requeued_and_completed_after_outage(client, settings, store, redis_server):
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    calls = []

    class DropsRedisOnce(_Ok):
        def run(self, request, recorder):
            calls.append(1)
            if len(calls) == 1:
                redis_server.connected = False       # Redis dies while the agent is running
            with recorder.step("classifier", "work"):
                pass
            return super().run(request, recorder)

    worker = _worker(settings, store, DropsRedisOnce(), on_sleep=lambda: setattr(redis_server, "connected", True))
    worker.loop_iteration()      # run is interrupted; job stays in the processing list
    assert client.get(f"/runs/{run_id}").json()["status"] == "running"
    worker.loop_iteration()      # Redis is back: job requeued and picked up again
    worker.loop_iteration()
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "escalated" and body["attempts"] == 2
    assert len(calls) == 2


def test_requeued_terminal_run_is_not_executed_twice(client, settings, store):
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    calls = []

    class Counting(_Ok):
        def run(self, request, recorder):
            calls.append(1)
            return super().run(request, recorder)

    worker = _worker(settings, store, Counting())
    worker.loop_iteration()
    assert len(calls) == 1
    # simulate "finished but ack lost": the same job reappears on the queue
    store.r.lpush("ecs:queue:runs", json.dumps({"run_id": run_id, "correlation_id": "x"}))
    worker.loop_iteration()
    assert len(calls) == 1
    assert client.get(f"/runs/{run_id}").json()["attempts"] == 1


def test_run_fails_after_max_attempts(client, settings, store):
    settings.run_max_attempts = 2
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    record = store.get(run_id)
    record.attempts = 2                         # already interrupted twice
    store.save(record)
    _worker(settings, store, _Ok()).loop_iteration()
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "failed" and body["result"] is None
    assert body["error"]["code"] == "max_attempts_exceeded"


def test_stub_runner_import_path_resolves():
    from app.worker.worker import load_runner

    runner = load_runner("app.agents.stub_runner:StubAgentRunner", llm=None, settings=None)
    assert isinstance(runner, StubAgentRunner)
    with pytest.raises((ImportError, AttributeError)):
        load_runner("app.agents.missing:Nope", llm=None, settings=None)
