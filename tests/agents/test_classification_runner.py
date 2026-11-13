"""Focused tests for the two-agent classification runner.

They drive the real ``RunRecorder`` (budgets, passage registry, citation resolution) with a scripted
model and a fake search function. The passages below are SYNTHETIC test fixtures. They are not tariff
text and prove nothing about real classification; the vacuum-flask case still has to be verified
against official passages once retrieval is integrated.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.classification_runner import ClassificationRunner
from app.agents.retrieval import RetrievalConfigError, load_retrieval_tool, normalise_passages
from app.common.config import Settings
from app.contracts import RunRequest
from app.contracts.results import DISCLAIMER
from app.worker.recorder import LoopBudgetExceeded, RunLimits, RunRecorder

PASSAGE_A = {
    "chunk_id": "chunk-a",
    "source_id": "test-source-1",
    "locator": "TEST Heading",
    "score": 0.9,
    "text": "TEST FIXTURE ONLY. This heading covers vacuum vessels complete with their cases.",
}
PASSAGE_B = {
    "chunk_id": "chunk-b",
    "source_id": "test-source-1",
    "locator": "TEST Note 1",
    "score": 0.8,
    "text": "TEST FIXTURE ONLY. This note says articles of mixed materials follow the material giving the essential character.",
}
QUOTE_A = "covers vacuum vessels complete with their cases"
QUOTE_B = "follow the material giving the essential character"

REQUEST = RunRequest(
    product_description="Stainless steel vacuum flask with a moulded plastic outer body.",
    materials=["stainless steel", "plastic"],
    destination_country="AE",
    quantity=500,
    quantity_unit="pcs",
    client_reference="PO-2026-0042",
)


def draft(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "outcome": "classify",
        "hs_code": "961700",
        "heading_title": "TEST heading",
        "gri_applied": ["GRI 1"],
        "notes_considered": [{"reference": "TEST note", "effect": "no effect on the result"}],
        "reasoning_summary": "Test reasoning.",
        "citations": [{"chunk_id": "chunk-a", "source_id": "test-source-1", "locator": "TEST Heading", "quote": QUOTE_A}],
        "export_policy": None,
        "clarification_questions": [],
        "reason": None,
    }
    body.update(overrides)
    return json.dumps(body)


def verdict(decision: str = "approve", **overrides: Any) -> str:
    body: dict[str, Any] = {
        "decision": decision,
        "code_supported": True,
        "gri_supported": True,
        "notes_supported": True,
        "citations_supported": True,
        "export_policy_supported": None,
        "issues": [],
        "follow_up_query": None,
        "reason": "",
    }
    if decision == "research":
        body["follow_up_query"] = "essential character mixed materials note"
        body["issues"] = ["No note on mixed materials was retrieved."]
        body["notes_supported"] = False
    body.update(overrides)
    return json.dumps(body)


class ScriptedLLM:
    """Stands in for LLMClient: returns the next scripted text and records every prompt."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], *, deadline: float, **params: Any) -> Any:
        self.prompts.append(messages)
        assert self.responses, "model called more often than the test scripted"
        return SimpleNamespace(
            text=self.responses.pop(0), provider="fake", model="fake", prompt_tokens=1, completion_tokens=1,
            cost_usd=0.0, attempts=1, fallback_used=False, latency_s=0.0,
        )

    def breaker_states(self) -> dict[str, str]:
        return {}


class FakeStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        self.events.append(event)


class FakeSearch:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.queries: list[str] = []

    def __call__(self, query: str, limit: int = 5) -> Any:
        self.queries.append(query)
        result = self.results.pop(0) if self.results else []
        if isinstance(result, Exception):
            raise result
        return result


def run_case(responses: list[str], search: Any, *, max_llm_calls: int = 10):
    llm = ScriptedLLM(responses)
    store = FakeStore()
    limits = RunLimits(max_steps=12, max_tool_calls=20, max_llm_calls=max_llm_calls, deadline=time.monotonic() + 30)
    recorder = RunRecorder(store, "run-test-0001", limits, llm, tool_timeout_s=5.0)
    runner = ClassificationRunner(llm=llm, settings=SimpleNamespace(retrieval_tool=""), search_fn=search)
    try:
        result = runner.run(REQUEST, recorder)
    finally:
        recorder.close()
    return result, recorder, llm, store


def assert_escalated_without_code(result, *, reason_contains: str) -> None:
    assert result.route == "specialist-classification-review"
    assert result.hs_code is None and result.citations == []
    assert result.verification.verified is False
    assert reason_contains.lower() in (result.escalation_reason or "").lower()
    assert result.disclaimer == DISCLAIMER


# ---------------------------------------------------------------- approval
def test_approval_returns_verified_code_and_hides_client_reference():
    search = FakeSearch([PASSAGE_A])
    result, rec, llm, store = run_case([draft(), verdict()], search)

    assert result.route == "clear-for-filing"
    assert result.hs_code == "961700"
    assert result.gri_applied == ["GRI 1"]
    assert result.verification.verified is True and result.verification.attempts == 1
    assert [c.chunk_id for c in result.citations] == ["chunk-a"]
    assert result.citations[0].source_id == "test-source-1"
    assert result.export_policy.policy == "Not determined"
    assert result.prompt_version == "classifier-v1+verifier-v1"
    assert result.disclaimer == DISCLAIMER
    assert (rec.llm_calls, rec.tool_calls) == (2, 1)

    sent = json.dumps(llm.prompts)
    assert "PO-2026-0042" not in sent and "client_reference" not in sent
    assert "500" not in json.dumps([m["content"] for p in llm.prompts for m in p if m["role"] == "user"])

    kinds = [e["kind"] for e in store.events]
    assert "handoff" in kinds and "search_strategy" in kinds and "verdict" in kinds
    handoff = next(e for e in store.events if e["kind"] == "handoff")
    assert (handoff["agent"], handoff["to"], handoff["citation_check"]) == ("classifier", "verifier", "passed")


# ---------------------------------------------------------------- invalid model output
@pytest.mark.parametrize(
    "bad_draft",
    [
        "I think the code is 9617.",
        "",
        "[1, 2, 3]",
        json.dumps({"outcome": "classify", "hs_code": "961700"}),  # missing required fields
        draft(gri_applied=["GRI 3(b)"]),  # GRI 1 not first
        draft(unexpected_field="x"),  # extra keys rejected
    ],
)
def test_invalid_classifier_output_escalates(bad_draft):
    result, rec, _, _ = run_case([bad_draft], FakeSearch([PASSAGE_A]))
    assert_escalated_without_code(result, reason_contains="could not be validated")
    assert rec.llm_calls == 1  # the verifier is never asked to bless garbage


def test_invalid_verifier_output_escalates():
    result, rec, _, _ = run_case([draft(), "approve!"], FakeSearch([PASSAGE_A]))
    assert_escalated_without_code(result, reason_contains="verifier output could not be validated")
    assert rec.llm_calls == 2


def test_inconsistent_verifier_approval_escalates():
    inconsistent = verdict("approve", citations_supported=False)
    result, _, _, _ = run_case([draft(), inconsistent], FakeSearch([PASSAGE_A]))
    assert_escalated_without_code(result, reason_contains="verifier output could not be validated")


def test_code_outside_supported_chapters_escalates():
    result, rec, _, _ = run_case([draft(hs_code="850100")], FakeSearch([PASSAGE_A]))
    assert_escalated_without_code(result, reason_contains="outside the supported chapters")
    assert rec.llm_calls == 1


# ---------------------------------------------------------------- failed citation verification
@pytest.mark.parametrize(
    "citation, expected",
    [
        ({"chunk_id": "chunk-a", "source_id": "test-source-1", "quote": "text that is not in the chunk"}, "quote does not appear"),
        ({"chunk_id": "chunk-zzz", "source_id": "test-source-1", "quote": QUOTE_A}, "was not retrieved"),
        ({"chunk_id": "chunk-a", "source_id": "some-other-source", "quote": QUOTE_A}, "does not match retrieved"),
    ],
)
def test_failed_citation_verification_escalates(citation, expected):
    result, rec, _, store = run_case([draft(citations=[citation])], FakeSearch([PASSAGE_A]))
    assert_escalated_without_code(result, reason_contains="citation verification failed")
    assert expected in result.escalation_reason
    assert rec.llm_calls == 1  # no verifier call for a draft that cannot be cited
    check = next(e for e in store.events if e["kind"] == "citation_check")
    assert check["passed"] is False


# ---------------------------------------------------------------- missing retrieval
def test_missing_retrieval_tool_escalates_without_model_calls():
    llm = ScriptedLLM([])
    recorder = RunRecorder(
        FakeStore(), "run-test-0002",
        RunLimits(max_steps=12, max_tool_calls=20, max_llm_calls=10, deadline=time.monotonic() + 30), llm, 5.0,
    )
    runner = ClassificationRunner(llm=llm, settings=SimpleNamespace(retrieval_tool=""))
    result = runner.run(REQUEST, recorder)
    recorder.close()
    assert_escalated_without_code(result, reason_contains="No retrieval tool is configured")
    assert (recorder.llm_calls, recorder.tool_calls) == (0, 0)


@pytest.mark.parametrize(
    "search_result, expected",
    [
        ([], "returned no passages"),
        ([{"chunk_id": "x"}, {"text": "no id"}, "junk"], "returned no passages"),
        (RuntimeError("index offline"), "Retrieval failed"),
    ],
)
def test_empty_or_failing_retrieval_escalates(search_result, expected):
    result, rec, _, _ = run_case([], FakeSearch(search_result))
    assert_escalated_without_code(result, reason_contains=expected)
    assert rec.llm_calls == 0


# ---------------------------------------------------------------- one successful follow-up search
def test_one_successful_follow_up_search_then_approval():
    first_draft = draft(notes_considered=[], reasoning_summary="No mixed-material note retrieved yet.")
    revised = draft(
        reasoning_summary="Revised using the mixed-materials note.",
        gri_applied=["GRI 1", "GRI 3(b)"],
        notes_considered=[{"reference": "TEST Note 1", "effect": "essential character decides"}],
        citations=[
            {"chunk_id": "chunk-a", "source_id": "test-source-1", "quote": QUOTE_A},
            {"chunk_id": "chunk-b", "source_id": "test-source-1", "locator": "TEST Note 1", "quote": QUOTE_B},
        ],
    )
    search = FakeSearch([PASSAGE_A], [PASSAGE_A, PASSAGE_B])
    result, rec, llm, store = run_case([first_draft, verdict("research"), revised, verdict()], search)

    assert result.route == "clear-for-filing" and result.hs_code == "961700"
    assert result.gri_applied == ["GRI 1", "GRI 3(b)"]
    assert {c.chunk_id for c in result.citations} == {"chunk-a", "chunk-b"}
    assert result.verification.attempts == 2
    assert len(result.verification.strategy_log) == 2
    assert result.verification.strategy_log[0].startswith("initial:")
    assert result.verification.strategy_log[1].startswith("follow-up:")
    assert len(search.queries) == 2 and search.queries[0] != search.queries[1]
    assert (rec.llm_calls, rec.tool_calls) == (4, 2)

    revise_prompt = json.loads(llm.prompts[2][1]["content"])
    assert revise_prompt["verifier_feedback"]["new_passages_from_follow_up"] == ["chunk-b"]
    handoffs = [(e["agent"], e["to"]) for e in store.events if e["kind"] == "handoff"]
    assert handoffs == [("classifier", "verifier"), ("verifier", "classifier"), ("classifier", "verifier")]
    strategies = [e for e in store.events if e["kind"] == "search_strategy"]
    assert [s["strategy"] for s in strategies] == ["initial", "follow-up"]


# ---------------------------------------------------------------- escalation after unresolved follow-up
def test_unresolved_follow_up_escalates():
    revised = draft(reasoning_summary="Still thin.")
    search = FakeSearch([PASSAGE_A], [PASSAGE_A, PASSAGE_B])
    result, rec, _, _ = run_case([draft(), verdict("research"), revised, verdict("research")], search)
    assert_escalated_without_code(result, reason_contains="not resolved after the single permitted follow-up")
    assert (rec.llm_calls, rec.tool_calls) == (4, 2)  # bounded: no second follow-up


def test_repeated_follow_up_query_escalates_without_second_search():
    search = FakeSearch([PASSAGE_A])
    first_query_text = " ".join([REQUEST.product_description, "stainless steel, plastic"])
    repeated = verdict("research", follow_up_query=first_query_text)
    result, rec, _, _ = run_case([draft(), repeated], search)
    assert_escalated_without_code(result, reason_contains="repeated an earlier search")
    assert (rec.llm_calls, rec.tool_calls) == (2, 1)


def test_follow_up_without_new_passages_escalates():
    search = FakeSearch([PASSAGE_A], [PASSAGE_A])
    result, rec, _, _ = run_case([draft(), verdict("research")], search)
    assert_escalated_without_code(result, reason_contains="no new passages")
    assert rec.llm_calls == 2


# ---------------------------------------------------------------- other outcomes and guarantees
def test_verifier_escalation_is_respected():
    result, _, _, _ = run_case([draft(), verdict("escalate", reason="Two headings fit equally.")], FakeSearch([PASSAGE_A]))
    assert_escalated_without_code(result, reason_contains="Two headings fit equally")


def test_needs_clarification_routes_without_code():
    clarify = draft(
        outcome="needs_clarification", hs_code=None, heading_title=None, gri_applied=[], citations=[],
        reasoning_summary="", notes_considered=[], clarification_questions=["Is the outer body fixed to the vessel?"],
        reason="Facts that decide the heading are missing.",
    )
    result, rec, _, _ = run_case([clarify], FakeSearch([PASSAGE_A]))
    assert result.route == "seek-product-clarification"
    assert result.hs_code is None and result.citations == []
    assert "outer body" in result.basis
    assert rec.llm_calls == 1


def test_unverified_export_policy_is_dropped_not_invented():
    with_policy = draft(export_policy={
        "policy": "Prohibited", "conditions": "invented", "citations": [
            {"chunk_id": "chunk-a", "source_id": "test-source-1", "quote": QUOTE_A}],
    })
    result, _, _, _ = run_case([with_policy, verdict(export_policy_supported=False)], FakeSearch([PASSAGE_A]))
    assert result.route == "clear-for-filing"
    assert result.export_policy.policy == "Not determined"
    assert "omitted" in result.verification.notes


def test_verified_export_policy_is_kept():
    with_policy = draft(export_policy={
        "policy": "Free", "conditions": None, "citations": [
            {"chunk_id": "chunk-a", "source_id": "test-source-1", "quote": QUOTE_A}],
    })
    result, _, _, _ = run_case([with_policy, verdict(export_policy_supported=True)], FakeSearch([PASSAGE_A]))
    assert result.export_policy.policy == "Free" and len(result.export_policy.citations) == 1


def test_budget_exhaustion_propagates_to_the_worker():
    with pytest.raises(LoopBudgetExceeded):
        run_case([draft(), verdict()], FakeSearch([PASSAGE_A]), max_llm_calls=1)


# ---------------------------------------------------------------- integration seam
def test_normalise_passages_drops_malformed_items():
    out = normalise_passages([
        {"chunk_id": 7, "text": "ok", "source_id": " s ", "score": 0.5},
        {"chunk_id": "7", "text": "duplicate id"},
        {"chunk_id": "", "text": "empty id"},
        {"chunk_id": "z", "text": "   "},
        "not a dict",
    ])
    assert out == [{"chunk_id": "7", "text": "ok", "source_id": "s", "locator": None, "score": 0.5}]
    assert normalise_passages(None) == []


def test_load_retrieval_tool():
    assert load_retrieval_tool("") is None
    assert callable(load_retrieval_tool("os.path:join"))
    for bad in ("no-colon", "nonexistent_module_xyz:fn", "os.path:does_not_exist"):
        with pytest.raises(RetrievalConfigError):
            load_retrieval_tool(bad)


def test_runner_fails_fast_on_broken_retrieval_config():
    with pytest.raises(RetrievalConfigError):
        ClassificationRunner(llm=None, settings=SimpleNamespace(retrieval_tool="nonexistent_module_xyz:fn"))


def test_stub_remains_the_default_runner(monkeypatch):
    monkeypatch.delenv("AGENT_RUNNER", raising=False)
    monkeypatch.delenv("RETRIEVAL_TOOL", raising=False)
    settings = Settings()
    assert settings.agent_runner == "app.agents.stub_runner:StubAgentRunner"
    assert settings.retrieval_tool == ""


def test_runner_matches_the_agent_runner_protocol():
    runner = ClassificationRunner(llm=None, settings=SimpleNamespace(retrieval_tool=""))
    assert runner.prompt_version == "classifier-v1+verifier-v1"
    assert callable(runner.run)
