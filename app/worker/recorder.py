"""Per-run recorder: loop bounds, timeouts, metrics, tracing and the reconstructible event log.

Every agent step, tool call (with query and returned passages) and model call (with prompt version,
tokens and cost) is appended to ``ecs:run:{run_id}:events``, so ``GET /runs/{run_id}/events``
reconstructs the whole run (D5). Limits on steps, tool calls, model calls and wall-clock time bound
the agent loop (A1).
"""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from opentelemetry.trace import Status, StatusCode

from app.common import metrics
from app.common.run_store import RunStore, utcnow
from app.common.tracing import tracer
from app.llm import DeadlineExceeded, LLMClient, LLMResponse, ProviderUnavailableError

log = logging.getLogger(__name__)
_MAX_REPR = 2000


class LoopBudgetExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ToolTimeoutError(TimeoutError):
    pass


@dataclass(frozen=True)
class RunLimits:
    max_steps: int
    max_tool_calls: int
    max_llm_calls: int
    deadline: float  # time.monotonic()


class RunRecorder:
    def __init__(
        self,
        store: RunStore,
        run_id: str,
        limits: RunLimits,
        llm: LLMClient | None,
        tool_timeout_s: float,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.limits = limits
        self.llm = llm
        self.tool_timeout_s = tool_timeout_s
        self.steps = 0
        self.tool_calls = 0
        self.llm_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.passages: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"tool-{run_id[:8]}")

    # ------------------------------------------------------------------ budget
    def remaining_s(self) -> float:
        return self.limits.deadline - time.monotonic()

    def _check_budget(self, kind: str) -> None:
        if self.remaining_s() <= 0:
            raise LoopBudgetExceeded("wall-clock limit reached")
        if kind == "step" and self.steps >= self.limits.max_steps:
            raise LoopBudgetExceeded(f"step limit ({self.limits.max_steps}) reached")
        if kind == "tool" and self.tool_calls >= self.limits.max_tool_calls:
            raise LoopBudgetExceeded(f"tool-call limit ({self.limits.max_tool_calls}) reached")
        if kind == "llm" and self.llm_calls >= self.limits.max_llm_calls:
            raise LoopBudgetExceeded(f"model-call limit ({self.limits.max_llm_calls}) reached")

    # ------------------------------------------------------------------ events
    def event(self, kind: str, agent: str | None = None, **data: Any) -> None:
        self._seq += 1
        self.store.append_event(
            self.run_id, {"seq": self._seq, "ts": utcnow().isoformat(), "kind": kind, "agent": agent, **data}
        )

    @contextmanager
    def step(self, agent: str, name: str, **data: Any) -> Iterator[None]:
        self._check_budget("step")
        self.steps += 1
        self.event("step_start", agent, step=name, **data)
        started = time.perf_counter()
        outcome = "success"
        with tracer().start_as_current_span(f"{agent}.{name}", attributes={"agent": agent, "step": name}) as span:
            try:
                yield
            except LoopBudgetExceeded:
                outcome = "budget_exceeded"
                raise
            except Exception as exc:
                outcome = "error"
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                latency = time.perf_counter() - started
                metrics.AGENT_STEPS.labels(agent, name, outcome).inc()
                metrics.AGENT_STEP_LATENCY.labels(agent, name).observe(latency)
                self.event("step_end", agent, step=name, outcome=outcome, latency_ms=round(latency * 1000, 1))

    # ------------------------------------------------------------------ tools
    def tool_call(
        self,
        agent: str,
        tool: str,
        fn: Callable[..., Any],
        /,
        *args: Any,
        query: Any = None,
        timeout_s: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run a tool with a timeout. Returned passages (``list[dict]`` with chunk_id/text) are registered."""
        self._check_budget("tool")
        self.tool_calls += 1
        timeout = min(timeout_s or self.tool_timeout_s, max(self.remaining_s(), 0.001))
        started = time.perf_counter()
        outcome = "success"
        result: Any = None
        with tracer().start_as_current_span(f"tool.{tool}", attributes={"agent": agent, "tool": tool}) as span:
            ctx = contextvars.copy_context()
            future = self._pool.submit(ctx.run, fn, *args, **kwargs)
            try:
                result = future.result(timeout=timeout)
            except FutureTimeout:
                outcome = "timeout"
                future.cancel()
                span.set_status(Status(StatusCode.ERROR, "timeout"))
                raise ToolTimeoutError(f"tool {tool} exceeded {timeout:.1f}s") from None
            except Exception as exc:
                outcome = "error"
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                latency = time.perf_counter() - started
                metrics.TOOL_CALLS.labels(agent, tool, outcome).inc()
                metrics.TOOL_LATENCY.labels(tool).observe(latency)
                passages = self._register_if_passages(result)
                self.event(
                    "tool_call", agent, tool=tool, query=query, outcome=outcome,
                    latency_ms=round(latency * 1000, 1),
                    passages=passages,
                    output=None if passages is not None else _short(result),
                )
        return result

    def _register_if_passages(self, result: Any) -> list[dict[str, Any]] | None:
        if not isinstance(result, list) or not result:
            return None
        if not all(isinstance(p, dict) and "chunk_id" in p and "text" in p for p in result):
            return None
        self.register_passages(result)
        return [
            {k: p.get(k) for k in ("chunk_id", "source_id", "score", "locator", "text") if k in p} for p in result
        ]

    def register_passages(self, passages: list[dict[str, Any]]) -> None:
        for p in passages:
            self.passages[str(p["chunk_id"])] = p

    def resolve_citation(self, chunk_id: str, quote: str) -> bool:
        """A citation resolves if its chunk was retrieved in this run and the quote occurs in it."""
        passage = self.passages.get(chunk_id)
        if not passage:
            return False
        return _normalise(quote) in _normalise(str(passage.get("text", "")))

    # ------------------------------------------------------------------ model
    def llm_call(
        self,
        agent: str,
        step: str,
        messages: list[dict[str, str]],
        *,
        prompt_version: str,
        **params: Any,
    ) -> LLMResponse:
        if self.llm is None:
            raise ProviderUnavailableError("no model client configured")
        self._check_budget("llm")
        self.llm_calls += 1
        with tracer().start_as_current_span(f"llm.{agent}.{step}", attributes={"agent": agent, "prompt_version": prompt_version}) as span:
            try:
                resp = self.llm.complete(messages, deadline=self.limits.deadline, **params)
            except DeadlineExceeded as exc:
                self.event("llm_call", agent, step=step, prompt_version=prompt_version, outcome="deadline")
                raise LoopBudgetExceeded("wall-clock limit reached") from exc
            except ProviderUnavailableError as exc:
                span.set_status(Status(StatusCode.ERROR, "provider unavailable"))
                self.event("llm_call", agent, step=step, prompt_version=prompt_version,
                           outcome="provider_unavailable", error=str(exc), breakers=self.llm.breaker_states())
                raise
            span.set_attributes({"provider": resp.provider, "prompt_tokens": resp.prompt_tokens,
                                 "completion_tokens": resp.completion_tokens, "cost_usd": resp.cost_usd})
        self.prompt_tokens += resp.prompt_tokens
        self.completion_tokens += resp.completion_tokens
        self.cost_usd += resp.cost_usd
        metrics.LLM_TOKENS.labels(resp.provider, agent, "prompt").inc(resp.prompt_tokens)
        metrics.LLM_TOKENS.labels(resp.provider, agent, "completion").inc(resp.completion_tokens)
        metrics.LLM_COST.labels(resp.provider, agent).inc(resp.cost_usd)
        self.event(
            "llm_call", agent, step=step, prompt_version=prompt_version, outcome="success",
            provider=resp.provider, model=resp.model, attempts=resp.attempts, fallback_used=resp.fallback_used,
            prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
            cost_usd=resp.cost_usd, latency_ms=round(resp.latency_s * 1000, 1),
            messages=messages, response=_short(resp.text),
        )
        return resp

    # ------------------------------------------------------------------ summary
    def usage(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 8),
        }

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _short(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= _MAX_REPR else text[:_MAX_REPR] + "...[truncated]"
