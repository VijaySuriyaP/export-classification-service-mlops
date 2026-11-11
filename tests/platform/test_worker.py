import time

import httpx
import pytest

from app.agents.stub_runner import StubAgentRunner
from app.contracts import Citation, ClassificationResult, Verification
from app.llm import LLMClient, ProviderConfig
from app.worker.worker import Worker

from .conftest import VACUUM_FLASK

PASSAGE = {"chunk_id": "ch96-9617", "source_id": "cbic-ch96",
           "text": "9617 Vacuum flasks and other vacuum vessels, complete; parts thereof other than glass inners."}


def _llm(handler):
    return LLMClient([ProviderConfig("primary", "http://stub.test/v1", "m")], timeout_s=1, max_attempts=2,
                     backoff_base_s=0, backoff_max_s=0, failure_threshold=2, reset_timeout_s=30,
                     http_client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def _ok(request):
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 2}})


def _submit(client):
    return client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]


def _drain(worker, store):
    raw = store.dequeue(worker.worker_id, timeout_s=0.1)
    assert raw
    import json
    worker.process(json.loads(raw))
    store.ack(worker.worker_id, raw)


class FakeRunner:
    """Simulates an agent: retrieves one passage and returns whatever result the test supplies."""
    prompt_version = "test-v1"

    def __init__(self, make_result, sleep_s=0.0):
        self.make_result = make_result
        self.sleep_s = sleep_s

    def run(self, request, recorder):
        with recorder.step("classifier", "search"):
            recorder.tool_call("classifier", "corpus_search", lambda: [PASSAGE], query="vacuum flask")
        time.sleep(self.sleep_s)
        return self.make_result()


def _result(**overrides):
    base = dict(route="clear-for-filing", basis="GRI 1: heading 9617 names vacuum flasks.", hs_code="96170012",
                gri_applied=["GRI 1", "GRI 6"],
                citations=[Citation(source_id="cbic-ch96", chunk_id="ch96-9617", locator="Heading 9617",
                                    quote="Vacuum flasks and other vacuum vessels, complete")],
                verification=Verification(verified=True, attempts=1), prompt_version="test-v1")
    base.update(overrides)
    return ClassificationResult(**base)


def _worker(settings, store, runner, llm=None):
    return Worker(settings, store, runner, llm, worker_id="test-worker")


def test_stub_runner_end_to_end(client, settings, store):
    run_id = _submit(client)
    _drain(_worker(settings, store, StubAgentRunner(llm=None, settings=settings), _llm(_ok)), store)
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "escalated"
    assert body["result"]["route"] == "specialist-classification-review"
    assert body["usage"]["llm_calls"] == 1 and body["usage"]["tool_calls"] == 1
    kinds = [e["kind"] for e in client.get(f"/runs/{run_id}/events").json()["events"]]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished" and "llm_call" in kinds


def test_verified_grounded_result_is_returned(client, settings, store):
    run_id = _submit(client)
    _drain(_worker(settings, store, FakeRunner(_result)), store)
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "completed"
    assert body["result"]["hs_code"] == "96170012"
    assert body["result"]["policy_adjustments"] == []
    events = client.get(f"/runs/{run_id}/events").json()["events"]
    tool = next(e for e in events if e["kind"] == "tool_call")
    assert tool["query"] == "vacuum flask" and tool["passages"][0]["chunk_id"] == "ch96-9617"


@pytest.mark.parametrize("overrides, rule", [
    ({"verification": Verification(verified=False)}, "unverified_code_withheld"),
    ({"citations": [Citation(source_id="x", chunk_id="never-retrieved", quote="made up")]}, "unresolved_citations"),
    ({"citations": [Citation(source_id="cbic-ch96", chunk_id="ch96-9617", quote="glass bottles of steel")]},
     "unresolved_citations"),
    ({"hs_code": "73239990"}, None),  # fine: chapter 73 is supported
    ({"hs_code": "84181000"}, "out_of_scope_chapter"),
])
def test_output_policy_blocks_unsupported_answers(client, settings, store, overrides, rule):
    run_id = _submit(client)
    _drain(_worker(settings, store, FakeRunner(lambda: _result(**overrides))), store)
    body = client.get(f"/runs/{run_id}").json()
    if rule is None:
        assert body["status"] == "completed"
        return
    assert body["status"] == "escalated"
    assert body["result"]["hs_code"] is None
    assert body["result"]["route"] == "specialist-classification-review"
    assert any(a.startswith(rule) for a in body["result"]["policy_adjustments"])


def test_provider_down_degrades_to_escalation(client, settings, store):
    run_id = _submit(client)
    llm = _llm(lambda r: httpx.Response(503))
    _drain(_worker(settings, store, StubAgentRunner(llm=llm, settings=settings), llm), store)
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "escalated"
    assert "provider unavailable" in body["result"]["escalation_reason"].lower()
    assert body["result"]["hs_code"] is None
    assert llm.breaker_states()["primary"] == "open"


def test_wall_clock_bound_escalates(client, settings, store):
    settings.run_wall_clock_timeout_s = 0.3
    run_id = _submit(client)
    _drain(_worker(settings, store, FakeRunner(_result, sleep_s=1.0)), store)
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "escalated"
    assert "budget exhausted" in body["result"]["escalation_reason"]


def test_step_limit_escalates(client, settings, store):
    settings.run_max_steps = 1

    class Looping(FakeRunner):
        def run(self, request, recorder):
            while True:
                with recorder.step("classifier", "search"):
                    pass

    run_id = _submit(client)
    _drain(_worker(settings, store, Looping(_result)), store)
    assert "step limit" in client.get(f"/runs/{run_id}").json()["result"]["escalation_reason"]


def test_agent_crash_marks_failed_not_answered(client, settings, store):
    class Crashing(FakeRunner):
        def run(self, request, recorder):
            raise ValueError("boom")

    run_id = _submit(client)
    _drain(_worker(settings, store, Crashing(_result)), store)
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "failed" and body["result"] is None
    assert body["error"]["code"] == "internal_error"


def test_dead_worker_jobs_are_requeued(client, store):
    _submit(client)
    raw = store.dequeue("dead-worker", timeout_s=0.1)   # taken, never acked, no heartbeat
    assert raw and store.queue_depth() == 0
    store.heartbeat("live-worker", 30)
    assert store.reap_dead_workers() == 1
    assert store.queue_depth() == 1


def test_run_cache_hit(client, settings, store):
    settings.run_cache_ttl_seconds = 60
    worker = _worker(settings, store, FakeRunner(_result))
    first, second = _submit(client), _submit(client)
    _drain(worker, store)
    _drain(worker, store)
    assert client.get(f"/runs/{first}").json()["cache_hit"] is False
    hit = client.get(f"/runs/{second}").json()
    assert hit["cache_hit"] is True and hit["result"]["hs_code"] == "96170012"
