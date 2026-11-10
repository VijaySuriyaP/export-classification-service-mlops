"""Output policy (A3 / L4): the platform's last check before a result is stored and returned.

Whatever the agents produce, the service is not authorised to:
  * issue ``clear-for-filing`` without a verified answer, a code and resolving citations;
  * cite text that was not retrieved during this run;
  * classify outside the supported chapters (F7).
Violations downgrade the result to ``specialist-classification-review`` and strip the unverified
code. Every adjustment is recorded on the result and counted in metrics.
"""

from __future__ import annotations

from typing import Protocol

from app.common import metrics
from app.contracts import SUPPORTED_CHAPTERS, ClassificationResult


class _Resolver(Protocol):
    def resolve_citation(self, chunk_id: str, quote: str) -> bool: ...


def enforce_output_policy(result: ClassificationResult, resolver: _Resolver) -> ClassificationResult:
    result = result.model_copy(deep=True)
    adjustments: list[str] = []

    # 1. Citations must resolve to text retrieved in this run.
    all_citations = list(result.citations) + (list(result.export_policy.citations) if result.export_policy else [])
    unresolved = [c for c in all_citations if not resolver.resolve_citation(c.chunk_id, c.quote)]
    if unresolved:
        ids = ", ".join(sorted({c.chunk_id for c in unresolved}))
        adjustments.append(f"unresolved_citations: {ids}")
        result.citations = [c for c in result.citations if c not in unresolved]
        if result.export_policy:
            result.export_policy.citations = [c for c in result.export_policy.citations if c not in unresolved]

    # 2. Supported chapters only.
    if result.hs_code and result.hs_code[:2] not in SUPPORTED_CHAPTERS:
        adjustments.append(f"out_of_scope_chapter: {result.hs_code[:2]}")

    # 3. A code may only be returned when verified and grounded.
    if result.hs_code and (not result.verification.verified or not result.citations):
        adjustments.append("unverified_code_withheld")

    # 4. clear-for-filing needs a verified, cited code.
    if result.route == "clear-for-filing" and (
        not result.verification.verified or not result.hs_code or not result.citations
    ):
        adjustments.append("clear_for_filing_requirements_not_met")

    if adjustments:
        for rule in adjustments:
            metrics.POLICY_ADJUSTMENTS.labels(rule.split(":")[0]).inc()
        downgrade = any(
            a.split(":")[0] in {"unresolved_citations", "out_of_scope_chapter", "unverified_code_withheld",
                                "clear_for_filing_requirements_not_met"}
            for a in adjustments
        )
        if downgrade and (result.route == "clear-for-filing" or result.hs_code):
            result.route = "specialist-classification-review"
            result.hs_code = None
            result.heading_title = None
            reason = "Output policy withheld the proposed code: " + "; ".join(adjustments)
            result.escalation_reason = f"{result.escalation_reason} | {reason}" if result.escalation_reason else reason
            result.basis = f"{result.basis} [downgraded by output policy]"
        result.policy_adjustments = [*result.policy_adjustments, *adjustments]
    return result


def run_status_for(result: ClassificationResult) -> str:
    """``escalated`` when a human specialist must decide, otherwise ``completed``."""
    return "escalated" if result.route == "specialist-classification-review" else "completed"
