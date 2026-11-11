"""Result data contract produced by the agent layer and returned by ``GET /runs/{run_id}``.

The agent runner (owned by the agent workstream) must return a :class:`ClassificationResult`.
The worker then applies the output policy (``app.guardrails.output_policy``) before anything is
stored, so an unverified or out-of-scope classification can never be returned as clear-for-filing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Route = Literal["clear-for-filing", "seek-product-clarification", "specialist-classification-review"]

DISCLAIMER = (
    "Proposed classification for decision support only. This service does not file declarations; "
    "a licensed customs broker makes the final classification decision."
)


class Citation(BaseModel):
    """A pointer to a retrieved passage. ``chunk_id`` and ``quote`` must resolve to retrieved text."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Corpus document identifier, e.g. 'cbic-tariff-ch96'.")
    chunk_id: str = Field(description="Identifier of the retrieved chunk the quote comes from.")
    locator: str | None = Field(default=None, description="Human locator, e.g. 'Heading 9617' or 'GRI 1'.")
    quote: str = Field(min_length=1, max_length=2000, description="Verbatim text from the retrieved chunk.")


class ExportPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["Free", "Restricted", "Prohibited", "STE", "Not determined"]
    conditions: str | None = None
    citations: list[Citation] = Field(default_factory=list)


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool
    attempts: int = Field(default=0, ge=0)
    strategy_log: list[str] = Field(default_factory=list, description="Search strategies tried, in order.")
    notes: str | None = None


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Route
    basis: str = Field(description="Why this route was chosen (F6).")
    hs_code: str | None = Field(default=None, pattern=r"^\d{4}(\d{2}){0,2}$")
    heading_title: str | None = None
    gri_applied: list[str] = Field(default_factory=list, description="Rules applied, in order, e.g. ['GRI 1', 'GRI 6'].")
    reasoning_summary: str = ""
    citations: list[Citation] = Field(default_factory=list)
    export_policy: ExportPolicy | None = None
    verification: Verification
    escalation_reason: str | None = None
    prompt_version: str
    policy_adjustments: list[str] = Field(
        default_factory=list, description="Changes made by the output policy before returning (L4)."
    )
    disclaimer: str = DISCLAIMER
