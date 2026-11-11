"""Edge cases of the ingestion boundary, rate limiter, quarantine, cache key and output policy."""

import json
import re

import pytest
from pydantic import ValidationError

from app.common.run_store import QUARANTINE_KEY, QUARANTINE_MAX, QuarantineStore, RateLimiter, RunStore
from app.contracts import Citation, ClassificationResult, ExportPolicy, Verification
from app.guardrails.output_policy import enforce_output_policy

from .conftest import VACUUM_FLASK


# ---------------------------------------------------------------- headers
def test_invalid_correlation_id_is_replaced(client):
    resp = client.get("/health", headers={"X-Correlation-ID": "bad id!\n"})
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Correlation-ID"])


def test_valid_correlation_id_is_echoed_and_stored(client, store):
    resp = client.post("/runs", json={"product_description": VACUUM_FLASK},
                       headers={"X-Correlation-ID": "trace-me.0001"})
    assert resp.headers["X-Correlation-ID"] == "trace-me.0001"
    assert store.get(resp.json()["run_id"]).correlation_id == "trace-me.0001"


def test_invalid_client_id_falls_back_without_error(client):
    resp = client.post("/runs", json={"product_description": VACUUM_FLASK}, headers={"X-Client-ID": "x y/../z"})
    assert resp.status_code == 202


# ---------------------------------------------------------------- body limits
def test_oversized_body_is_quarantined(client, settings, redis_client):
    settings.max_request_bytes = 200
    resp = client.post("/runs", json={"product_description": VACUUM_FLASK * 10})
    assert resp.status_code == 422 and resp.json()["errors"][0]["type"] == "too_large"
    assert redis_client.llen("ecs:queue:runs") == 0


def test_non_object_json_is_rejected(client):
    for body in ("[]", '"text"', "123", "null"):
        resp = client.post("/runs", content=body, headers={"Content-Type": "application/json"})
        assert resp.status_code == 422, body
        assert resp.json()["run_created"] is False


def test_multiple_errors_are_all_reported(client):
    resp = client.post("/runs", json={"product_description": 5, "materials": "steel", "quantity": -1,
                                      "destination_country": "uae", "x": 1})
    fields = {e["field"] for e in resp.json()["errors"]}
    assert {"product_description", "materials", "quantity", "destination_country", "x"} <= fields


# ---------------------------------------------------------------- quarantine
def test_quarantine_record_has_errors_and_truncated_raw_body(client, redis_client):
    client.post("/runs", content=b'{"product_description": "' + b"a" * 6000 + b'"', headers={"Content-Type": "application/json"})
    entry = json.loads(redis_client.lindex(QUARANTINE_KEY, 0))
    assert entry["reason"] == "malformed_json" and entry["errors"][0]["field"] == "body"
    assert len(entry["raw_body"]) <= 4096 and entry["received_at"] and entry["quarantine_id"]


def test_quarantine_is_bounded(redis_client):
    quarantine = QuarantineStore(redis_client)
    for i in range(QUARANTINE_MAX + 25):
        quarantine.add({"reason": "t", "n": i})
    assert quarantine.count() == QUARANTINE_MAX
    assert quarantine.recent(1)[0]["n"] == QUARANTINE_MAX + 24     # newest first


def test_quarantine_endpoint_limit_validated(client):
    assert client.get("/quarantine?limit=0").status_code == 422
    assert client.get("/quarantine?limit=500").status_code == 422
    assert client.get("/quarantine?limit=5").json()["total"] == 0


# ---------------------------------------------------------------- rate limiter
def test_rate_limit_window_rolls_over(redis_client):
    now = [600.0]
    limiter = RateLimiter(redis_client, per_minute=2, clock=lambda: now[0])
    assert limiter.check("c") == (True, 0)
    assert limiter.check("c") == (True, 0)
    allowed, retry_after = limiter.check("c")
    assert not allowed and retry_after == 60
    now[0] = 630.0
    assert limiter.check("c") == (False, 30)          # same window, shorter wait
    now[0] = 660.0
    assert limiter.check("c") == (True, 0)             # new window


def test_rate_limit_counts_rejected_requests_too(client):
    headers = {"X-Client-ID": "abuser"}
    codes = [client.post("/runs", json={"product_description": "x"}, headers=headers).status_code for _ in range(7)]
    assert codes == [422] * 5 + [429] * 2             # bad requests also cost budget


def test_rate_limit_can_be_disabled(settings, redis_client):
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    settings.rate_limit_enabled = False
    app_client = TestClient(create_app(settings, redis_client))
    codes = {app_client.post("/runs", json={"product_description": VACUUM_FLASK}).status_code for _ in range(10)}
    assert codes == {202}


# ---------------------------------------------------------------- cache key
def test_cache_key_ignores_reference_case_and_whitespace():
    base = {"product_description": "Steel flask", "client_reference": "A"}
    same = {"product_description": "  steel   FLASK ", "client_reference": "B"}
    assert RunStore.cache_key(base, "v1") == RunStore.cache_key(same, "v1")


def test_cache_key_changes_with_prompt_version_and_content():
    base = {"product_description": "Steel flask"}
    assert RunStore.cache_key(base, "v1") != RunStore.cache_key(base, "v2")
    assert RunStore.cache_key(base, "v1") != RunStore.cache_key({**base, "materials": ["steel"]}, "v1")


def test_degraded_runs_are_not_cached(client, settings, store):
    import httpx

    from app.agents.stub_runner import StubAgentRunner
    from app.llm import LLMClient, ProviderConfig
    from app.worker.worker import Worker

    settings.run_cache_ttl_seconds = 60
    llm = LLMClient([ProviderConfig("primary", "http://x.test/v1", "m")], timeout_s=1, max_attempts=1,
                    backoff_base_s=0, backoff_max_s=0, failure_threshold=5, reset_timeout_s=30,
                    http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
                    sleep=lambda s: None)
    worker = Worker(settings, store, StubAgentRunner(llm=llm, settings=settings), llm, "w")
    worker.poll_timeout_s = 0.05
    client.post("/runs", json={"product_description": VACUUM_FLASK})
    worker.loop_iteration()
    assert not list(store.r.scan_iter("ecs:cache:*"))


# ---------------------------------------------------------------- contracts / output policy
class _Resolver:
    def __init__(self, known):
        self.known = known

    def resolve_citation(self, chunk_id, quote):
        return self.known.get(chunk_id) == quote


def _result(**kw):
    base = dict(route="clear-for-filing", basis="b", hs_code="96170012", prompt_version="v",
                citations=[Citation(source_id="s", chunk_id="c1", quote="q1")],
                verification=Verification(verified=True))
    base.update(kw)
    return ClassificationResult(**base)


def test_unresolved_export_policy_citation_is_stripped():
    result = _result(export_policy=ExportPolicy(policy="Free", citations=[
        Citation(source_id="s", chunk_id="c1", quote="q1"), Citation(source_id="s", chunk_id="c9", quote="fake")]))
    out = enforce_output_policy(result, _Resolver({"c1": "q1"}))
    assert [c.chunk_id for c in out.export_policy.citations] == ["c1"]
    assert out.route == "specialist-classification-review" and out.hs_code is None   # any bad citation downgrades
    assert any(a.startswith("unresolved_citations") for a in out.policy_adjustments)


def test_clarification_route_without_code_passes_untouched():
    result = _result(route="seek-product-clarification", hs_code=None, citations=[],
                     verification=Verification(verified=False))
    out = enforce_output_policy(result, _Resolver({}))
    assert out.route == "seek-product-clarification" and out.policy_adjustments == []


def test_policy_does_not_mutate_its_input():
    result = _result(hs_code="84181000")
    enforce_output_policy(result, _Resolver({"c1": "q1"}))
    assert result.hs_code == "84181000" and result.route == "clear-for-filing"


@pytest.mark.parametrize("code", ["961", "96170", "9617001", "ab170012", "9617.00"])
def test_malformed_hs_codes_are_rejected_by_contract(code):
    with pytest.raises(ValidationError):
        _result(hs_code=code)


def test_contract_forbids_unknown_result_fields():
    with pytest.raises(ValidationError):
        _result(confidence=0.99)
