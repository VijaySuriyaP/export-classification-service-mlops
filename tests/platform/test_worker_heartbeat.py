"""Long-running jobs must not be mistaken for jobs from dead workers."""

import threading
import time

from app.contracts import ClassificationResult, Verification
from app.worker.worker import Worker

from .conftest import VACUUM_FLASK


def test_busy_worker_heartbeat_prevents_requeue(client, settings, store):
    settings.worker_heartbeat_ttl_s = 1
    settings.run_wall_clock_timeout_s = 10
    started = threading.Event()
    release = threading.Event()

    class SlowRunner:
        prompt_version = "test"

        def run(self, request, recorder):
            started.set()
            assert release.wait(5)
            return ClassificationResult(
                route="specialist-classification-review",
                basis="test",
                verification=Verification(verified=False),
                prompt_version="test",
            )

    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    worker = Worker(settings, store, SlowRunner(), None, "busy-worker")
    worker.poll_timeout_s = 0.05
    thread = threading.Thread(target=worker.loop_iteration)
    thread.start()
    try:
        assert started.wait(3)
        time.sleep(1.4)
        assert store.reap_dead_workers() == 0
        assert store.queue_depth() == 0
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert store.get(run_id).attempts == 1


def test_expired_worker_job_is_requeued(client, store):
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    store.heartbeat("dead-worker", 1)
    raw = store.dequeue("dead-worker", timeout_s=0.1)
    assert raw is not None
    time.sleep(1.2)
    assert store.reap_dead_workers() == 1
    assert store.queue_depth() == 1
    assert run_id in store.dequeue("replacement-worker", timeout_s=0.1)
