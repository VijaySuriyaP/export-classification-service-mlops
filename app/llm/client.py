"""Model provider interface we control (brief section 12, A5, L5).

Any OpenAI-compatible chat-completions endpoint works (hosted free tiers, Ollama, or our own
``llm-stub``), so the provider is switched purely by configuration.

Degradation path for one call:
    primary provider  --(timeout / 5xx / 429, bounded retries with backoff)-->
    fallback provider (if configured, same policy)                        -->
    ProviderUnavailableError -> the worker escalates the run to specialist review, never a guess.
Each provider has its own circuit breaker; an open breaker skips the provider without waiting.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.common import metrics
from app.common.resilience import CircuitBreaker, backoff_delay

log = logging.getLogger(__name__)


class ProviderUnavailableError(RuntimeError):
    """No provider could serve the call (all failed or all circuits open)."""


class LLMRequestError(RuntimeError):
    """The provider rejected the request itself (4xx other than 429). Not retried."""


class DeadlineExceeded(TimeoutError):
    """The run's wall-clock budget does not allow another call."""


class _Retryable(Exception):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    price_prompt_per_1k: float = 0.0
    price_completion_per_1k: float = 0.0


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_s: float
    provider: str
    model: str
    attempts: int
    fallback_used: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class LLMClient:
    def __init__(
        self,
        providers: list[ProviderConfig],
        *,
        timeout_s: float,
        max_attempts: int,
        backoff_base_s: float,
        backoff_max_s: float,
        failure_threshold: int,
        reset_timeout_s: float,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.providers = providers
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self._http = http_client or httpx.Client()
        self._sleep = sleep
        self._clock = clock
        self.breakers = {
            p.name: CircuitBreaker(
                p.name,
                failure_threshold,
                reset_timeout_s,
                clock=clock,
                on_change=lambda name, state: metrics.CIRCUIT_STATE.labels(name).set(
                    CircuitBreaker._GAUGE[state]
                ),
            )
            for p in providers
        }
        for p in providers:
            metrics.CIRCUIT_STATE.labels(p.name).set(0)

    @classmethod
    def from_settings(cls, s, http_client: httpx.Client | None = None) -> "LLMClient":
        providers: list[ProviderConfig] = []
        if s.llm_base_url:
            providers.append(
                ProviderConfig("primary", s.llm_base_url, s.llm_model, s.llm_api_key,
                               s.llm_price_prompt_per_1k, s.llm_price_completion_per_1k)
            )
        if s.llm_fallback_base_url:
            providers.append(
                ProviderConfig("fallback", s.llm_fallback_base_url, s.llm_fallback_model, s.llm_fallback_api_key,
                               s.llm_fallback_price_prompt_per_1k, s.llm_fallback_price_completion_per_1k)
            )
        return cls(
            providers,
            timeout_s=s.llm_timeout_s,
            max_attempts=s.llm_max_attempts,
            backoff_base_s=s.llm_backoff_base_s,
            backoff_max_s=s.llm_backoff_max_s,
            failure_threshold=s.cb_failure_threshold,
            reset_timeout_s=s.cb_reset_timeout_s,
            http_client=http_client,
        )

    def breaker_states(self) -> dict[str, str]:
        return {name: b.state for name, b in self.breakers.items()}

    # ------------------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        deadline: float | None = None,
    ) -> LLMResponse:
        """Run one chat completion through the degradation path. ``deadline`` is a ``time.monotonic`` value."""
        errors: list[str] = []
        for index, provider in enumerate(self.providers):
            breaker = self.breakers[provider.name]
            for attempt in range(1, self.max_attempts + 1):
                if not breaker.allow():
                    metrics.CIRCUIT_REJECTIONS.labels(provider.name).inc()
                    errors.append(f"{provider.name}: circuit open")
                    break
                timeout = self._call_timeout(deadline)
                started = time.perf_counter()
                try:
                    data = self._post(provider, messages, temperature, max_tokens, response_format, timeout)
                except _Retryable as exc:
                    elapsed = time.perf_counter() - started
                    breaker.record_failure()
                    metrics.LLM_CALLS.labels(provider.name, "error").inc()
                    metrics.LLM_LATENCY.labels(provider.name).observe(elapsed)
                    errors.append(f"{provider.name} attempt {attempt}: {exc}")
                    log.warning("model call failed", extra={"provider": provider.name, "attempt": attempt, "error": str(exc)})
                    if attempt >= self.max_attempts or breaker.state == CircuitBreaker.OPEN:
                        break
                    delay = backoff_delay(attempt, self.backoff_base_s, self.backoff_max_s)
                    if deadline is not None and self._clock() + delay >= deadline:
                        break
                    metrics.LLM_RETRIES.labels(provider.name).inc()
                    self._sleep(delay)
                    continue
                except LLMRequestError:
                    breaker.record_success()  # provider is up; the request was bad
                    metrics.LLM_CALLS.labels(provider.name, "rejected").inc()
                    raise

                latency = time.perf_counter() - started
                breaker.record_success()
                metrics.LLM_CALLS.labels(provider.name, "success").inc()
                metrics.LLM_LATENCY.labels(provider.name).observe(latency)
                if index > 0:
                    metrics.LLM_FALLBACKS.inc()
                return self._to_response(provider, data, latency, attempt, index > 0, messages)
        raise ProviderUnavailableError("; ".join(errors) or "no model provider configured")

    # ------------------------------------------------------------------
    def _call_timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self.timeout_s
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise DeadlineExceeded("run wall-clock budget exhausted before model call")
        return min(self.timeout_s, remaining)

    def _post(self, p: ProviderConfig, messages, temperature, max_tokens, response_format, timeout) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": p.model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}
        try:
            resp = self._http.post(p.base_url.rstrip("/") + "/chat/completions", json=payload,
                                   headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise _Retryable(f"timeout after {timeout:.1f}s") from exc
        except httpx.TransportError as exc:
            raise _Retryable(f"connection error: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _Retryable(f"http {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMRequestError(f"http {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
            data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _Retryable("malformed response") from exc
        return data

    @staticmethod
    def _to_response(p: ProviderConfig, data, latency, attempts, fallback, messages) -> LLMResponse:
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or sum(len(m.get("content", "")) for m in messages) // 4)
        completion_tokens = int(usage.get("completion_tokens") or len(text) // 4)
        cost = prompt_tokens / 1000 * p.price_prompt_per_1k + completion_tokens / 1000 * p.price_completion_per_1k
        return LLMResponse(text, prompt_tokens, completion_tokens, round(cost, 8), latency,
                           p.name, data.get("model") or p.model, attempts, fallback, data)
