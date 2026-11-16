"""Generate deterministic LLM-stub responses using passages returned by the real index.

Run INSIDE the worker image before starting llm-stub, using the load-test Compose overlay.
This is a performance fixture, not evidence of semantic verification or tariff accuracy.
The actual LangGraph runner, retrieval, citation checks, queue and tracing remain in use.
"""

import json
from pathlib import Path

from app.retrieval.tool import search

DESCRIPTION = "Stainless steel vacuum flask with a moulded plastic outer body."


def build_responses(passages):
    candidates = [
        p for p in passages
        if p.get("source_id", "").startswith("customs-tariff-chapter-96")
        and "vacuum" in p["text"][:3000].lower()
    ]
    if not candidates:
        raise RuntimeError("Real retrieval did not return a Chapter 96 vacuum passage; aborting.")
    passage = candidates[0]
    text = passage["text"][:3000]
    start = text.lower().index("vacuum")
    quote = text[start:start + 150]
    citation = {k: passage[k] for k in ("chunk_id", "source_id")}
    citation.update(locator=passage.get("locator"), quote=quote)
    return {
        "[[stub:classifier]]": {
            "outcome": "classify", "hs_code": "9617",
            "heading_title": "PERFORMANCE FIXTURE: vacuum vessels",
            "gri_applied": ["GRI 1"], "notes_considered": [],
            "reasoning_summary": "Scripted load-test response; not a legal determination.",
            "citations": [citation], "export_policy": None,
            "clarification_questions": [], "reason": None,
        },
        "[[stub:verifier]]": {
            "decision": "approve", "code_supported": True, "gri_supported": True,
            "notes_supported": True, "citations_supported": True,
            "export_policy_supported": None, "issues": [], "follow_up_query": None,
            "reason": "Scripted performance fixture, not semantic evaluation.",
        },
    }


if __name__ == "__main__":
    responses = build_responses(search(DESCRIPTION, limit=5))
    out = Path("/srv/loadtest-fixtures")
    out.mkdir(parents=True, exist_ok=True)
    (out / "responses.json").write_text(json.dumps(responses, indent=2), encoding="utf-8")
    print("Generated two canned responses from real retrieved text. PERFORMANCE TEST ONLY.")
