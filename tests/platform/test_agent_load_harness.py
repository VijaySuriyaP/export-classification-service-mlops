"""Performance fixtures exercise agent plumbing, never semantic accuracy."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.agents.classification_runner import ClassificationRunner
from app.common.config import Settings
from app.contracts import RunRequest
from app.worker.recorder import RunLimits, RunRecorder
from tests.agents.test_classification_runner import FakeStore, ScriptedLLM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


def test_real_graph_consumes_two_canned_model_responses(monkeypatch):
    # Avoid loading the embedding model in unit tests; performance jobs use the real adapter.
    import types
    import time

    fake = types.ModuleType("app.retrieval.tool")
    fake.search = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "app.retrieval.tool", fake)
    fixture = module("prepare_agent_load_fixture")
    passage = {
        "chunk_id": "fixture-96", "source_id": "customs-tariff-chapter-96-test",
        "text": "SYNTHETIC vacuum vessel performance fixture, not legal text.",
    }
    responses = fixture.build_responses([passage])
    llm = ScriptedLLM([json.dumps(v) for v in responses.values()])
    rec = RunRecorder(FakeStore(), "test", RunLimits(12, 20, 10, time.monotonic()+10), llm, 5)
    try:
        runner = ClassificationRunner(llm=llm, settings=Settings(), search_fn=lambda *a, **k: [passage])
        result = runner.run(RunRequest(product_description=fixture.DESCRIPTION), rec)
        assert result.verification.verified
        assert rec.llm_calls == 2 and rec.tool_calls == 1
        assert "PERFORMANCE" in result.heading_title
        with pytest.raises(RuntimeError, match="abort"):
            fixture.build_responses([])
    finally:
        rec.close()


def test_counter_deltas_and_reset_detection():
    runner = module("run_loadtest")
    assert runner.accounting_delta({"runs": 1}, {"runs": 4}) == {"runs": 3}
    with pytest.raises(RuntimeError, match="reset"):
        runner.accounting_delta({"runs": 5}, {"runs": 1})


def test_agent_report_rejects_incomplete_matrix(tmp_path):
    report = module("agent_load_report")
    with pytest.raises(ValueError, match="Incomplete"):
        report.build_report(tmp_path)
