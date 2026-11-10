"""OpenAI-compatible LLM stub (D6, A5 demo).

Returns fixed responses after a realistic delay, so load tests measure our system rather than a
provider's rate limits. Failure modes can be switched at runtime to demonstrate the circuit breaker:

    POST /admin/mode {"mode": "down"}               -> every call returns 503
    POST /admin/mode {"mode": "slow"}               -> every call hangs past the client timeout
    POST /admin/mode {"mode": "ok", "error_rate": 0.2} -> 20% random 503s
    POST /admin/mode {"mode": "ok"}                 -> healthy again

Canned responses: set STUB_RESPONSES_FILE to a JSON object mapping a marker substring (searched in
the system/user messages, e.g. "[[stub:classifier]]") to the content to return.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

LATENCY_MS = float(os.getenv("STUB_LATENCY_MS", "1500"))
JITTER_MS = float(os.getenv("STUB_LATENCY_JITTER_MS", "500"))
SLOW_S = float(os.getenv("STUB_SLOW_SECONDS", "120"))
DEFAULT_CONTENT = os.getenv("STUB_DEFAULT_CONTENT", "OK")

_CANNED: dict[str, str] = {}
if os.getenv("STUB_RESPONSES_FILE"):
    with open(os.environ["STUB_RESPONSES_FILE"], encoding="utf-8") as fh:
        _CANNED = {k: v if isinstance(v, str) else json.dumps(v) for k, v in json.load(fh).items()}

CALLS = Counter("ecs_stub_calls_total", "Stub calls", ["outcome"])
LATENCY = Histogram("ecs_stub_latency_seconds", "Stub response delay")
MODE = Gauge("ecs_stub_mode", "0=ok 1=down 2=slow")


class ModeUpdate(BaseModel):
    mode: Literal["ok", "down", "slow"]
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)


state: dict[str, Any] = {"mode": "ok", "error_rate": 0.0}
app = FastAPI(title="LLM stub", version="1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", **state}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/mode")
def get_mode() -> dict:
    return state


@app.post("/admin/mode")
def set_mode(update: ModeUpdate) -> dict:
    state.update(update.model_dump())
    MODE.set({"ok": 0, "down": 1, "slow": 2}[update.mode])
    return state


def _pick_content(messages: list[dict[str, Any]]) -> str:
    joined = "\n".join(str(m.get("content", "")) for m in messages)
    for marker, content in _CANNED.items():
        if marker in joined:
            return content
    return DEFAULT_CONTENT


@app.post("/v1/chat/completions")
async def chat_completions(body: dict[str, Any]):
    if state["mode"] == "down":
        CALLS.labels("down").inc()
        return JSONResponse(status_code=503, content={"error": {"message": "stub: provider down"}})
    if state["mode"] == "slow":
        CALLS.labels("slow").inc()
        await asyncio.sleep(SLOW_S)
    if random.random() < state["error_rate"]:
        CALLS.labels("injected_error").inc()
        return JSONResponse(status_code=503, content={"error": {"message": "stub: injected error"}})

    delay = max(0.0, random.gauss(LATENCY_MS, JITTER_MS / 2)) / 1000
    LATENCY.observe(delay)
    await asyncio.sleep(delay)
    messages = body.get("messages") or []
    content = _pick_content(messages)
    prompt_tokens = max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4)
    completion_tokens = max(1, len(content) // 4)
    CALLS.labels("ok").inc()
    return {
        "id": f"stub-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or "stub",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }
