import time

import httpx
import pytest

from app.common.resilience import CircuitBreaker, backoff_delay
from app.llm import LLMClient, LLMRequestError, ProviderConfig, ProviderUnavailableError


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_breaker_opens_half_opens_and_closes():
    clock = FakeClock()
    b = CircuitBreaker("p", failure_threshold=3, reset_timeout_s=30, clock=clock)
    for _ in range(3):
        assert b.allow()
        b.record_failure()
    assert b.state == "open" and not b.allow()
    clock.t += 30
    assert b.state == "half_open"
    assert b.allow() and not b.allow()          # exactly one trial
    b.record_failure()
    assert b.state == "open"
    clock.t += 30
    assert b.allow()
    b.record_success()
    assert b.state == "closed"


def test_backoff_is_exponential_and_capped():
    assert backoff_delay(1, 1, 100, rng=lambda: 1.0) == 1
    assert backoff_delay(3, 1, 100, rng=lambda: 1.0) == 4
    assert backoff_delay(10, 1, 8, rng=lambda: 1.0) == 8
    assert backoff_delay(3, 1, 100, rng=lambda: 0.0) == 2


def _client(handler, providers=("primary",), threshold=5, attempts=3, sleeps=None):
    cfgs = [ProviderConfig(name, f"http://{name}.test/v1", "m", price_prompt_per_1k=1.0,
                           price_completion_per_1k=2.0) for name in providers]
    return LLMClient(cfgs, timeout_s=1, max_attempts=attempts, backoff_base_s=0.1, backoff_max_s=1,
                     failure_threshold=threshold, reset_timeout_s=30,
                     http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=(sleeps.append if sleeps is not None else lambda s: None))


def _ok(content="OK"):
    return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": content}}],
                                     "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})


def test_retries_with_backoff_then_succeeds():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else _ok()

    resp = _client(handler, sleeps=sleeps).complete([{"role": "user", "content": "hi"}])
    assert resp.attempts == 3 and len(sleeps) == 2
    assert resp.cost_usd == pytest.approx(1.0 + 1.0)   # 1k prompt * $1 + 0.5k completion * $2


def test_timeout_is_retryable():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return _ok()

    assert _client(handler).complete([{"role": "user", "content": "hi"}]).attempts == 2


def test_falls_back_to_secondary_provider():
    def handler(request):
        return httpx.Response(503) if request.url.host == "primary.test" else _ok("from fallback")

    resp = _client(handler, providers=("primary", "fallback")).complete([{"role": "user", "content": "hi"}])
    assert resp.fallback_used and resp.provider == "fallback" and resp.text == "from fallback"


def test_circuit_opens_and_provider_unavailable_raised():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    client = _client(handler, threshold=3, attempts=3)
    with pytest.raises(ProviderUnavailableError):
        client.complete([{"role": "user", "content": "hi"}])
    assert client.breaker_states()["primary"] == "open"
    calls.clear()
    with pytest.raises(ProviderUnavailableError, match="circuit open"):
        client.complete([{"role": "user", "content": "hi"}])
    assert calls == []      # open breaker fails fast, no call made


def test_client_error_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="bad request")

    with pytest.raises(LLMRequestError):
        _client(handler).complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 1


def test_no_provider_configured():
    with pytest.raises(ProviderUnavailableError):
        _client(lambda r: _ok(), providers=()).complete([{"role": "user", "content": "hi"}])


def test_deadline_respected():
    from app.llm import DeadlineExceeded

    with pytest.raises(DeadlineExceeded):
        _client(lambda r: _ok()).complete([{"role": "user", "content": "hi"}], deadline=time.monotonic() - 1)
