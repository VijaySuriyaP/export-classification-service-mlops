"""Two-agent (classifier -> verifier) LangGraph runner for export classification.

Workflow (bounded; at most 6 steps, 4 model calls, 2 searches):

    initial_search -> draft -> verify --approve--------------------------> result
                                 |  \\--escalate / invalid / unresolved ---> escalation
                                 \\--research (once) -> followup_search -> revise -> verify_final -> result

Safety properties (see docs/classification-agents.md):

* every model call goes through ``recorder.llm_call`` and every search through ``recorder.tool_call``;
* a code is returned only after (a) the draft passes schema and scope checks, (b) every citation
  resolves to text retrieved in this run, with matching source identity, and (c) the verifier
  approves the same draft;
* invalid model output, missing/empty retrieval, failed citations, an unresolved follow-up or a
  repeated query all end in ``specialist-classification-review`` with no code;
* ``client_reference`` and quantities are never placed in a prompt.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents.classification_prompts import (
    CLASSIFIER_PROMPT_VERSION,
    COMBINED_PROMPT_VERSION,
    VERIFIER_PROMPT_VERSION,
    build_classifier_messages,
    build_verifier_messages,
    model_passages,
)
from app.agents.classification_schemas import (
    CitationIn,
    DraftOutput,
    ModelOutputError,
    VerifierOutput,
    parse_model_json,
)
from app.agents.retrieval import DEFAULT_LIMIT, load_retrieval_tool, normalise_passages
from app.contracts import ClassificationResult, RunRequest
from app.contracts.requests import SUPPORTED_CHAPTERS
from app.contracts.results import Citation, ExportPolicy, Verification
from app.worker.recorder import LoopBudgetExceeded

if TYPE_CHECKING:
    from app.common.config import Settings
    from app.llm import LLMClient
    from app.worker.recorder import RunRecorder

log = logging.getLogger(__name__)

_ROUTE_ESCALATE = "specialist-classification-review"
_ROUTE_CLARIFY = "seek-product-clarification"
_QUERY_MAX_CHARS = 400


class _State(TypedDict, total=False):
    product: dict[str, Any]
    passages: dict[str, dict[str, Any]]  # chunk_id -> passage, insertion ordered
    search_log: list[dict[str, Any]]
    draft: DraftOutput
    verdicts: list[VerifierOutput]
    followup_done: bool
    halt: dict[str, Any]  # {"route": "escalate"|"clarify", "reason": str, "questions": list[str]}


def _escalate(reason: str) -> dict[str, Any]:
    return {"route": "escalate", "reason": reason}


def _query_key(query: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", query.lower()))


def _rec(config: RunnableConfig) -> "RunRecorder":
    return config["configurable"]["recorder"]


def _halt_or(next_node: str):
    def route(state: _State) -> str:
        return "end" if state.get("halt") else next_node

    return route


def _after_verify(state: _State) -> str:
    if state.get("halt"):
        return "end"
    return "followup_search" if state["verdicts"][-1].decision == "research" else "end"


def _citation_failures(rec: "RunRecorder", citations: list[CitationIn]) -> list[str]:
    """Deterministic check: chunk retrieved in this run, same source, quote present in that chunk."""
    failures: list[str] = []
    for c in citations:
        passage = rec.passages.get(c.chunk_id)
        if passage is None:
            failures.append(f"{c.chunk_id}: chunk was not retrieved in this run")
            continue
        retrieved_source = passage.get("source_id")
        if retrieved_source and retrieved_source != c.source_id:
            failures.append(f"{c.chunk_id}: source_id {c.source_id!r} does not match retrieved {retrieved_source!r}")
            continue
        if not rec.resolve_citation(c.chunk_id, c.quote):
            failures.append(f"{c.chunk_id}: quote does not appear in the retrieved text")
    return failures


def _to_citation(rec: "RunRecorder", c: CitationIn) -> Citation:
    locator = c.locator or rec.passages.get(c.chunk_id, {}).get("locator")
    return Citation(source_id=c.source_id, chunk_id=c.chunk_id, locator=locator, quote=c.quote)


class ClassificationRunner:
    """AgentRunner implementation: ``Runner(llm=..., settings=...)`` then ``run(request, recorder)``."""

    prompt_version = COMBINED_PROMPT_VERSION

    def __init__(self, *, llm: "LLMClient", settings: "Settings", search_fn: Any = None) -> None:
        # Model calls go through recorder.llm_call, which holds the client; ``llm`` is kept for parity
        # with the AgentRunner protocol. ``search_fn`` lets tests inject a search without an env var.
        self._llm = llm
        self._settings = settings
        self._search = search_fn if search_fn is not None else load_retrieval_tool(getattr(settings, "retrieval_tool", ""))
        self._graph = self._build_graph()
        log.info("classification runner ready", extra={"retrieval_configured": self._search is not None})

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g = StateGraph(_State)
        g.add_node("initial_search", self._initial_search)
        g.add_node("draft", self._draft_node)
        g.add_node("verify", self._verify_node)
        g.add_node("followup_search", self._followup_node)
        g.add_node("revise", self._revise_node)
        g.add_node("verify_final", self._verify_final_node)
        g.add_edge(START, "initial_search")
        g.add_conditional_edges("initial_search", _halt_or("draft"), {"draft": "draft", "end": END})
        g.add_conditional_edges("draft", _halt_or("verify"), {"verify": "verify", "end": END})
        g.add_conditional_edges(
            "verify", _after_verify, {"followup_search": "followup_search", "end": END}
        )
        g.add_conditional_edges("followup_search", _halt_or("revise"), {"revise": "revise", "end": END})
        g.add_conditional_edges("revise", _halt_or("verify_final"), {"verify_final": "verify_final", "end": END})
        g.add_edge("verify_final", END)
        return g.compile()

    def run(self, request: RunRequest, recorder: "RunRecorder") -> ClassificationResult:
        state: _State = {
            "product": self._product_payload(request),
            "passages": {},
            "search_log": [],
            "verdicts": [],
            "followup_done": False,
        }
        final = self._graph.invoke(
            state, config={"configurable": {"recorder": recorder}, "recursion_limit": 25}
        )
        return self._build_result(recorder, final)

    @staticmethod
    def _product_payload(request: RunRequest) -> dict[str, Any]:
        """The only request data a model sees. No client_reference, no quantity."""
        return {
            **request.model_text_fields(),
            "destination_country": request.destination_country,
            "submitter_hints (advisory, unverified)": {
                "chapter": request.chapter_hint,
                "hs_code": request.hs_code_hint,
            },
        }

    # ------------------------------------------------------------------ search
    def _run_search(
        self, rec: "RunRecorder", agent: str, query: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        try:
            raw = rec.tool_call(agent, "corpus_search", self._search, query, query=query, limit=DEFAULT_LIMIT)
        except LoopBudgetExceeded:
            raise
        except Exception as exc:  # timeout or retrieval error: escalate with a reason, never guess
            return [], f"Retrieval failed ({type(exc).__name__}); no evidence available."
        passages = normalise_passages(raw)
        rec.register_passages(passages)  # make sure what the models saw is what citations resolve against
        return passages, None

    @staticmethod
    def _merge(
        known: dict[str, dict[str, Any]], found: list[dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[str], str | None]:
        merged = dict(known)
        new_ids: list[str] = []
        for p in found:
            cid = p["chunk_id"]
            if cid in merged:
                if merged[cid]["text"] != p["text"] or merged[cid].get("source_id") != p.get("source_id"):
                    return known, [], f"Retrieval returned inconsistent content for chunk {cid}."
                continue
            merged[cid] = p
            new_ids.append(cid)
        return merged, new_ids, None

    def _log_search(
        self, rec: "RunRecorder", state: _State, agent: str, strategy: str, query: str, rationale: str,
        returned: int, new_ids: list[str],
    ) -> list[dict[str, Any]]:
        entry = {
            "attempt": len(state.get("search_log", [])) + 1,
            "strategy": strategy,
            "query": query,
            "rationale": rationale,
            "returned": returned,
            "new_chunk_ids": new_ids,
        }
        rec.event("search_strategy", agent, **entry)
        return [*state.get("search_log", []), entry]

    # ------------------------------------------------------------------ nodes
    def _initial_search(self, state: _State, config: RunnableConfig) -> dict[str, Any]:
        rec = _rec(config)
        with rec.step("classifier", "initial_search"):
            if self._search is None:
                return {"halt": _escalate(
                    "No retrieval tool is configured (RETRIEVAL_TOOL is empty), so there is no official "
                    "evidence to classify against.")}
            product = state["product"]
            query = " ".join(
                part for part in (product["product_description"], product.get("materials", "")) if part
            )[:_QUERY_MAX_CHARS]
            found, error = self._run_search(rec, "classifier", query)
            if error:
                return {"halt": _escalate(error)}
            merged, new_ids, conflict = self._merge({}, found)
            log_ = self._log_search(
                rec, state, "classifier", "initial", query,
                "deterministic query from the submitted description and materials", len(found), new_ids,
            )
            if conflict:
                return {"halt": _escalate(conflict), "search_log": log_}
            if not merged:
                return {"halt": _escalate("The initial search returned no passages, so there is no evidence to classify against."),
                        "search_log": log_}
            return {"passages": merged, "search_log": log_}

    def _draft_node(self, state: _State, config: RunnableConfig) -> dict[str, Any]:
        return self._draft(state, config, "draft", revision=False)

    def _revise_node(self, state: _State, config: RunnableConfig) -> dict[str, Any]:
        return self._draft(state, config, "revise", revision=True)

    def _draft(self, state: _State, config: RunnableConfig, step_name: str, *, revision: bool) -> dict[str, Any]:
        rec = _rec(config)
        with rec.step("classifier", step_name):
            feedback = None
            if revision:
                last = state["verdicts"][-1]
                feedback = {
                    "issues": last.issues,
                    "reason": last.reason,
                    "follow_up_query": last.follow_up_query,
                    "new_passages_from_follow_up": state["search_log"][-1]["new_chunk_ids"],
                }
            messages = build_classifier_messages(
                state["product"],
                model_passages(state["passages"]),
                previous_draft=state["draft"].model_dump(mode="json") if revision else None,
                verifier_feedback=feedback,
            )
            resp = rec.llm_call("classifier", step_name, messages, prompt_version=CLASSIFIER_PROMPT_VERSION)
            try:
                draft = parse_model_json(resp.text, DraftOutput)
            except ModelOutputError as exc:
                return {"halt": _escalate(f"Classifier output could not be validated ({exc}); no classification returned.")}
            return self._accept_draft(rec, step_name, draft)

    def _accept_draft(self, rec: "RunRecorder", stage: str, draft: DraftOutput) -> dict[str, Any]:
        if draft.outcome == "needs_clarification":
            return {"draft": draft, "halt": {
                "route": "clarify",
                "reason": draft.reason or "More product information is needed.",
                "questions": draft.clarification_questions,
            }}
        if draft.outcome == "out_of_scope":
            return {"draft": draft, "halt": _escalate(
                f"The product appears to be outside the supported scope (chapters {' and '.join(SUPPORTED_CHAPTERS)}): "
                f"{draft.reason or 'no detail given'}")}
        if draft.outcome == "insufficient_evidence":
            return {"draft": draft, "halt": _escalate(
                f"The retrieved passages do not support a classification: {draft.reason or 'no detail given'}")}

        assert draft.hs_code is not None  # guaranteed by DraftOutput for outcome == "classify"
        if draft.hs_code[:2] not in SUPPORTED_CHAPTERS:
            return {"draft": draft, "halt": _escalate(
                f"The proposed code falls outside the supported chapters ({', '.join(SUPPORTED_CHAPTERS)}); "
                "no code returned.")}
        failures = _citation_failures(rec, draft.citations)
        if failures:
            rec.event("citation_check", "classifier", stage=stage, passed=False, failures=failures)
            return {"draft": draft, "halt": _escalate(
                "Citation verification failed (" + "; ".join(failures[:3]) + "); no code returned.")}

        rec.event("citation_check", "classifier", stage=stage, passed=True, failures=[])
        rec.event(
            "handoff", "classifier", to="verifier", stage=stage, outcome=draft.outcome, hs_code=draft.hs_code,
            gri_applied=draft.gri_applied, notes=[n.reference for n in draft.notes_considered],
            cited_chunk_ids=[c.chunk_id for c in draft.citations], citation_check="passed",
        )
        return {"draft": draft}

    def _verify_node(self, state: _State, config: RunnableConfig) -> dict[str, Any]:
        return self._verify(state, config, "verify", final=False)

    def _verify_final_node(self, state: _State, config: RunnableConfig) -> dict[str, Any]:
        return self._verify(state, config, "verify_final", final=True)

    def _verify(self, state: _State, config: RunnableConfig, step_name: str, *, final: bool) -> dict[str, Any]:
        rec = _rec(config)
        with rec.step("verifier", step_name):
            draft = state["draft"]
            messages = build_verifier_messages(
                state["product"],
                model_passages(state["passages"]),
                draft.model_dump(mode="json"),
                citation_check={
                    "all_quotes_found_in_cited_chunks": True,
                    "cited_chunk_ids": [c.chunk_id for c in draft.citations],
                },
                search_log=state["search_log"],
            )
            resp = rec.llm_call("verifier", step_name, messages, prompt_version=VERIFIER_PROMPT_VERSION)
            try:
                verdict = parse_model_json(resp.text, VerifierOutput)
            except ModelOutputError as exc:
                return {"halt": _escalate(f"Verifier output could not be validated ({exc}); no classification returned.")}

            verdicts = [*state.get("verdicts", []), verdict]
            rec.event(
                "verdict", "verifier", stage=step_name, decision=verdict.decision,
                checks={
                    "code": verdict.code_supported, "gri": verdict.gri_supported,
                    "notes": verdict.notes_supported, "citations": verdict.citations_supported,
                    "export_policy": verdict.export_policy_supported,
                },
                issues=verdict.issues, follow_up_query=verdict.follow_up_query,
            )
            update: dict[str, Any] = {"verdicts": verdicts}
            if verdict.decision == "escalate":
                detail = verdict.reason or "; ".join(verdict.issues) or "no detail given"
                update["halt"] = _escalate(f"Verifier escalated: {detail}")
            elif verdict.decision == "research" and (final or state.get("followup_done")):
                update["halt"] = _escalate(
                    "Verification was not resolved after the single permitted follow-up search "
                    f"({verdict.reason or '; '.join(verdict.issues) or 'verifier still requested research'}).")
            return update

    def _followup_node(self, state: _State, config: RunnableConfig) -> dict[str, Any]:
        rec = _rec(config)
        with rec.step("verifier", "followup_search"):
            verdict = state["verdicts"][-1]
            query = (verdict.follow_up_query or "").strip()[:_QUERY_MAX_CHARS]
            rec.event(
                "handoff", "verifier", to="classifier", stage="followup_search", decision="research",
                issues=verdict.issues, follow_up_query=query,
            )
            tried = {_query_key(e["query"]) for e in state.get("search_log", [])}
            if not query or _query_key(query) in tried:
                return {"halt": _escalate("The verifier's follow-up query repeated an earlier search, so no new evidence could be sought.")}
            found, error = self._run_search(rec, "verifier", query)
            if error:
                return {"halt": _escalate(error)}
            merged, new_ids, conflict = self._merge(state["passages"], found)
            log_ = self._log_search(
                rec, state, "verifier", "follow-up", query,
                verdict.reason or "; ".join(verdict.issues) or "verifier requested targeted search",
                len(found), new_ids,
            )
            if conflict:
                return {"halt": _escalate(conflict), "search_log": log_}
            if not new_ids:
                return {"halt": _escalate("The follow-up search returned no new passages, so the open issues could not be resolved."),
                        "search_log": log_}
            return {"passages": merged, "search_log": log_, "followup_done": True}

    # ------------------------------------------------------------------ result
    def _verification(self, state: _State, *, verified: bool, notes: str) -> Verification:
        strategy_log = [
            f"{e['strategy']}: {e['query']!r} -> {e['returned']} passage(s), {len(e['new_chunk_ids'])} new"
            for e in state.get("search_log", [])
        ]
        return Verification(
            verified=verified, attempts=len(state.get("verdicts", [])), strategy_log=strategy_log, notes=notes
        )

    def _escalation(self, state: _State, reason: str) -> ClassificationResult:
        return ClassificationResult(
            route=_ROUTE_ESCALATE,
            basis=reason,
            verification=self._verification(state, verified=False, notes="No verified classification; routed to specialist review."),
            escalation_reason=reason,
            prompt_version=self.prompt_version,
        )

    def _build_result(self, rec: "RunRecorder", state: _State) -> ClassificationResult:
        halt = state.get("halt")
        if halt:
            if halt["route"] == "clarify":
                questions = halt.get("questions") or []
                detail = "; ".join(questions) if questions else halt["reason"]
                return ClassificationResult(
                    route=_ROUTE_CLARIFY,
                    basis=f"Additional product information is required before a classification can be proposed: {detail}",
                    verification=self._verification(state, verified=False, notes="No code proposed; clarification requested."),
                    prompt_version=self.prompt_version,
                )
            return self._escalation(state, halt["reason"])

        draft = state.get("draft")
        verdicts = state.get("verdicts") or []
        if draft is None or draft.outcome != "classify" or not verdicts or verdicts[-1].decision != "approve":
            return self._escalation(state, "The workflow ended without a verified classification.")
        failures = _citation_failures(rec, draft.citations)  # final gate on exactly what is being returned
        if failures:
            return self._escalation(state, "Citation verification failed at the final check; no code returned.")

        export, export_note = self._export_policy(rec, draft, verdicts[-1])
        notes = ["Independently approved by the verifier against the retrieved passages."]
        if export_note:
            notes.append(export_note)
        note_lines = [f"{n.reference}: {n.effect}" for n in draft.notes_considered]
        reasoning = draft.reasoning_summary + (("\n\nNotes considered:\n- " + "\n- ".join(note_lines)) if note_lines else "")
        return ClassificationResult(
            route="clear-for-filing",
            basis=(
                f"Proposed code {draft.hs_code} ({draft.heading_title}) reached by applying "
                f"{', '.join(draft.gri_applied)}; every citation resolves to retrieved text and the verifier approved the result."
            ),
            hs_code=draft.hs_code,
            heading_title=draft.heading_title,
            gri_applied=list(draft.gri_applied),
            reasoning_summary=reasoning,
            citations=[_to_citation(rec, c) for c in draft.citations],
            export_policy=export,
            verification=self._verification(state, verified=True, notes=" ".join(notes)),
            prompt_version=self.prompt_version,
        )

    def _export_policy(
        self, rec: "RunRecorder", draft: DraftOutput, verdict: VerifierOutput
    ) -> tuple[ExportPolicy, str | None]:
        ep = draft.export_policy
        undetermined = ExportPolicy(policy="Not determined")
        if ep is None or ep.policy == "Not determined":
            return undetermined, None
        if not ep.citations or _citation_failures(rec, ep.citations) or verdict.export_policy_supported is not True:
            return undetermined, "An export-policy finding was proposed but not verified, so it was omitted."
        return ExportPolicy(
            policy=ep.policy, conditions=ep.conditions, citations=[_to_citation(rec, c) for c in ep.citations]
        ), None
