"""Feedback endpoint (L6): only finished runs, field-level validation, de-duplication, aggregation."""

from app.common.run_store import summarise_feedback
from app.contracts import ClassificationResult, Verification

from .conftest import VACUUM_FLASK


def _finished_run(client, store, *, status="escalated", prompt_version="stub-v0", client_id="web-a") -> str:
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK},
                         headers={"X-Client-ID": client_id}).json()["run_id"]
    record = store.get(run_id)
    record.status = status
    record.result = ClassificationResult(
        route="specialist-classification-review", basis="test", prompt_version=prompt_version,
        verification=Verification(verified=False), escalation_reason="test")
    store.save(record)
    return run_id


def test_feedback_on_finished_run_is_stored_and_stamped(client, store):
    run_id = _finished_run(client, store)
    resp = client.post(f"/runs/{run_id}/feedback", headers={"X-Client-ID": "reviewer-1"},
                       json={"rating": "down", "reasons": ["wrong_route", "wrong_route"], "comment": "  needs notes  "})
    assert resp.status_code == 201
    ack = resp.json()
    assert ack["run_id"] == run_id and ack["replaced_previous"] is False

    items = client.get(f"/runs/{run_id}/feedback").json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["reasons"] == ["wrong_route"]                       # de-duplicated
    assert item["comment"] == "needs notes"                         # trimmed
    assert item["prompt_version"] == "stub-v0"                      # stamped from the run
    assert item["route"] == "specialist-classification-review"
    assert item["run_status"] == "escalated"


def test_feedback_rejected_while_run_not_finished(client):
    run_id = client.post("/runs", json={"product_description": VACUUM_FLASK}).json()["run_id"]
    resp = client.post(f"/runs/{run_id}/feedback", json={"rating": "up"})
    assert resp.status_code == 409
    assert resp.json() == {"error": "run_not_finished", "run_id": run_id, "status": "queued"}


def test_feedback_unknown_run_and_bad_id(client):
    assert client.post("/runs/" + "0" * 32 + "/feedback", json={"rating": "up"}).status_code == 404
    assert client.post("/runs/nope/feedback", json={"rating": "up"}).status_code == 422


def test_feedback_validation_returns_field_errors(client, store):
    run_id = _finished_run(client, store)
    resp = client.post(f"/runs/{run_id}/feedback",
                       json={"rating": "meh", "reasons": ["bogus"], "suggested_hs_code": "96A7", "extra": 1})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_failed"
    assert {e["field"] for e in body["errors"]} == {"rating", "reasons.0", "suggested_hs_code", "extra"}

    bad_json = client.post(f"/runs/{run_id}/feedback", content=b"{oops", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 422 and bad_json.json()["errors"][0]["type"] == "json_invalid"

    ctrl = client.post(f"/runs/{run_id}/feedback", json={"rating": "up", "comment": "bad\x00byte"})
    assert ctrl.status_code == 422


def test_rerating_replaces_instead_of_double_counting(client, store):
    run_id = _finished_run(client, store)
    headers = {"X-Client-ID": "reviewer-1"}
    client.post(f"/runs/{run_id}/feedback", json={"rating": "down"}, headers=headers)
    second = client.post(f"/runs/{run_id}/feedback", json={"rating": "up"}, headers=headers).json()
    assert second["replaced_previous"] is True
    client.post(f"/runs/{run_id}/feedback", json={"rating": "down"}, headers={"X-Client-ID": "reviewer-2"})

    summary = client.get("/feedback/summary").json()
    assert summary["total"] == {"total": 2, "up": 1, "down": 1, "satisfaction": 0.5}


def test_summary_groups_by_prompt_version_route_and_day(client, store):
    a = _finished_run(client, store, prompt_version="classifier-v1")
    b = _finished_run(client, store, prompt_version="classifier-v2")
    client.post(f"/runs/{a}/feedback", json={"rating": "down", "reasons": ["missing_notes"]})
    client.post(f"/runs/{b}/feedback", json={"rating": "up"})

    s = client.get("/feedback/summary").json()
    assert s["by_prompt_version"]["classifier-v1"]["down"] == 1
    assert s["by_prompt_version"]["classifier-v2"]["satisfaction"] == 1.0
    assert s["by_route"]["specialist-classification-review"]["total"] == 2
    assert sum(day["total"] for day in s["by_day"].values()) == 2
    assert s["reasons"] == {"missing_notes": 1}
    assert len(s["recent"]) == 2
    assert "ecs_feedback_total" in client.get("/metrics").text


def test_summarise_feedback_empty_and_ordering():
    empty = summarise_feedback([])
    assert empty["total"] == {"total": 0, "up": 0, "down": 0, "satisfaction": None}
    entries = [
        {"rating": "up", "received_at": "2026-11-02T10:00:00+00:00", "prompt_version": "v1", "route": "r"},
        {"rating": "down", "received_at": "2026-11-01T10:00:00+00:00", "prompt_version": None, "route": None},
    ]
    s = summarise_feedback(entries, recent=1)
    assert list(s["by_day"]) == ["2026-11-01", "2026-11-02"]
    assert s["by_prompt_version"]["unknown"]["down"] == 1 and s["by_route"]["none"]["down"] == 1
    assert s["recent"][0]["received_at"].startswith("2026-11-02")
