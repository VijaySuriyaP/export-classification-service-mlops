"""Placeholder agent runner used until the real agents are integrated.

It exercises every platform path a real run uses - two named agents, a tool call, a model call
through the resilient client, the event log and metrics - but it never proposes a classification.
It always escalates, so it can never be mistaken for a real answer.
"""

from __future__ import annotations

from app.contracts import ClassificationResult, RunRequest, Verification


class StubAgentRunner:
    prompt_version = "stub-v0"

    def __init__(self, *, llm, settings) -> None:
        self.llm = llm
        self.settings = settings

    def run(self, request: RunRequest, recorder) -> ClassificationResult:
        with recorder.step("classifier", "read_request"):
            fields = recorder.tool_call(
                "classifier", "request_field_lookup", lambda: request.model_dump(exclude_none=True),
                query="all fields",
            )
        with recorder.step("classifier", "draft"):
            recorder.llm_call(
                "classifier",
                "draft",
                [
                    {"role": "system", "content": "[[stub:classifier]] Platform smoke test. Reply with OK."},
                    {"role": "user", "content": f"<product>{fields['product_description']}</product>"},
                ],
                prompt_version=self.prompt_version,
                max_tokens=16,
            )
        with recorder.step("verifier", "verify"):
            pass
        return ClassificationResult(
            route="specialist-classification-review",
            basis="Platform stub runner: the classification agents are not integrated yet.",
            verification=Verification(verified=False, attempts=0, notes="stub - nothing to verify"),
            escalation_reason="No classification performed (stub agent).",
            prompt_version=self.prompt_version,
        )
