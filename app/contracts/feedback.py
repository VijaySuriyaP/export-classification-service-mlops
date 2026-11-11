"""Feedback contract (L6): a reviewer's verdict on a finished run, aggregated for quality over time.

Feedback is about the *result*, so it can only be given once a run is terminal. Each entry is
stamped with the run's route and ``prompt_version``, so the summary can compare prompt versions
and show whether quality changes after a deliberate change (L3/L6).

Free text in feedback is stored for humans only. It is never sent to a model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Rating = Literal["up", "down"]
FeedbackReason = Literal[
    "wrong_hs_code",        # the proposed code is wrong
    "wrong_route",          # e.g. should have escalated, or escalated needlessly
    "citation_problem",     # a citation does not support the claim
    "missing_notes",        # a section/chapter note that changes the outcome was ignored
    "unclear_explanation",  # basis / reasoning summary hard to follow
    "too_slow",
    "other",
]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rating: Rating
    reasons: list[FeedbackReason] = Field(default_factory=list, max_length=7)
    comment: str | None = Field(default=None, max_length=1000)
    suggested_hs_code: str | None = Field(default=None, pattern=r"^\d{4}(\d{2}){0,2}$",
                                          description="What the reviewer believes the code should be.")
    reviewer: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._ \-]+$")

    @field_validator("reasons")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("comment")
    @classmethod
    def _no_control_chars(cls, v: str | None) -> str | None:
        if v and any(ord(c) < 32 and c not in "\n\t" for c in v):
            raise ValueError("comment contains control characters")
        return v or None


class FeedbackRecord(FeedbackRequest):
    feedback_id: str
    run_id: str
    client_id: str
    received_at: datetime
    run_status: str
    route: str | None = None
    prompt_version: str | None = None
    hs_code: str | None = None


class FeedbackAck(BaseModel):
    feedback_id: str
    run_id: str
    replaced_previous: bool = Field(description="True if this client had already rated this run; the new entry wins.")


class FeedbackBucket(BaseModel):
    total: int = 0
    up: int = 0
    down: int = 0
    satisfaction: float | None = Field(default=None, description="up / total, null when total is 0.")


class FeedbackSummary(BaseModel):
    total: FeedbackBucket
    by_prompt_version: dict[str, FeedbackBucket]
    by_route: dict[str, FeedbackBucket]
    by_day: dict[str, FeedbackBucket] = Field(description="UTC date (YYYY-MM-DD) -> bucket: quality over time.")
    reasons: dict[str, int]
    recent: list[FeedbackRecord]
