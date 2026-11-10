"""The integration seam between the platform and the agent workstream.

The worker loads the class named by ``AGENT_RUNNER`` (``module.path:ClassName``), constructs it once
per worker process as ``Runner(llm=LLMClient, settings=Settings)`` and calls ``run`` for every job.

Rules for implementations (see docs/platform.md, "Integrating the agents"):

* call models only through ``recorder.llm_call`` and tools only through ``recorder.tool_call``
  - this gives timeouts, retries, circuit breaking, cost accounting, tracing and the run event log;
* wrap each agent step in ``with recorder.step(agent, name):``;
* retrieval tools should return ``list[dict]`` with at least ``chunk_id`` and ``text`` (plus
  ``source_id``) so the platform can check that every citation resolves to retrieved text;
* ``LoopBudgetExceeded`` and ``ProviderUnavailableError`` may simply propagate - the worker turns
  them into a specialist-review escalation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.contracts import ClassificationResult, RunRequest

if TYPE_CHECKING:
    from app.common.config import Settings
    from app.llm import LLMClient
    from app.worker.recorder import RunRecorder


class AgentRunner(Protocol):
    #: Recorded with every response and part of the run-cache key (L2).
    prompt_version: str

    def __init__(self, *, llm: "LLMClient", settings: "Settings") -> None: ...

    def run(self, request: RunRequest, recorder: "RunRecorder") -> ClassificationResult: ...
