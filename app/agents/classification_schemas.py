"""Validated shapes for what the classifier and verifier models are allowed to return.

Model output is untrusted. ``parse_model_json`` accepts only a single JSON object (optionally inside
one markdown fence) that passes these schemas; anything else raises ``ModelOutputError`` and the
runner escalates instead of guessing.
"""

from __future__ import annotations

import json
import re
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

HS_CODE_PATTERN = r"^\d{4}(\d{2}){0,2}$"
_GRI_RE = re.compile(r"^GRI ([1-6])(\([a-c]\))?$")
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_MAX_MODEL_TEXT = 100_000


class ModelOutputError(ValueError):
    """The model returned something that is not a valid, schema-conforming JSON object."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CitationIn(_Model):
    chunk_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    locator: str | None = Field(default=None, max_length=200)
    quote: str = Field(min_length=1, max_length=1000)


class NoteUsed(_Model):
    reference: str = Field(min_length=1, max_length=200, description="e.g. 'Chapter 96 Note 3'")
    effect: str = Field(min_length=1, max_length=600, description="How the note bears on the result.")


class ExportPolicyIn(_Model):
    policy: Literal["Free", "Restricted", "Prohibited", "STE", "Not determined"]
    conditions: str | None = Field(default=None, max_length=1000)
    citations: list[CitationIn] = Field(default_factory=list, max_length=6)


class DraftOutput(_Model):
    outcome: Literal["classify", "needs_clarification", "out_of_scope", "insufficient_evidence"]
    hs_code: str | None = Field(default=None, pattern=HS_CODE_PATTERN)
    heading_title: str | None = Field(default=None, max_length=300)
    gri_applied: list[str] = Field(default_factory=list, max_length=8)
    notes_considered: list[NoteUsed] = Field(default_factory=list, max_length=10)
    reasoning_summary: str = Field(default="", max_length=3000)
    citations: list[CitationIn] = Field(default_factory=list, max_length=12)
    export_policy: ExportPolicyIn | None = None
    clarification_questions: list[str] = Field(default_factory=list, max_length=5)
    reason: str | None = Field(default=None, max_length=500, description="Why, for non-classify outcomes.")

    @field_validator("clarification_questions")
    @classmethod
    def _short_questions(cls, value: list[str]) -> list[str]:
        if any(not q.strip() or len(q) > 300 for q in value):
            raise ValueError("clarification questions must be 1-300 characters")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "DraftOutput":
        if self.outcome != "classify":
            if self.hs_code:
                raise ValueError("hs_code must be null unless outcome is 'classify'")
            if not (self.reason or self.clarification_questions):
                raise ValueError("a non-classify outcome needs a reason or clarification questions")
            return self

        problems: list[str] = []
        if not self.hs_code:
            problems.append("hs_code is required")
        if not self.heading_title:
            problems.append("heading_title is required")
        if not self.reasoning_summary.strip():
            problems.append("reasoning_summary is required")
        if not self.citations:
            problems.append("at least one citation is required")
        if not self.gri_applied:
            problems.append("gri_applied is required")
        else:
            numbers: list[int] = []
            for rule in self.gri_applied:
                match = _GRI_RE.match(rule)
                if not match:
                    problems.append(f"invalid GRI entry {rule!r}")
                    break
                numbers.append(int(match.group(1)))
            else:
                if numbers[0] != 1:
                    problems.append("GRI 1 must be applied first")
                if numbers != sorted(numbers):
                    problems.append("GRI rules must be listed in the order applied")
                if len(set(self.gri_applied)) != len(self.gri_applied):
                    problems.append("duplicate GRI entries")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class VerifierOutput(_Model):
    decision: Literal["approve", "research", "escalate"]
    code_supported: bool
    gri_supported: bool
    notes_supported: bool
    citations_supported: bool
    export_policy_supported: bool | None = None
    issues: list[str] = Field(default_factory=list, max_length=10)
    follow_up_query: str | None = Field(default=None, max_length=300)
    reason: str = Field(default="", max_length=1000)

    @field_validator("issues")
    @classmethod
    def _short_issues(cls, value: list[str]) -> list[str]:
        if any(len(i) > 500 for i in value):
            raise ValueError("issues must be at most 500 characters each")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "VerifierOutput":
        if self.decision == "approve" and not (
            self.code_supported and self.gri_supported and self.notes_supported and self.citations_supported
        ):
            raise ValueError("approve requires code, GRI, notes and citations to all be supported")
        if self.decision == "research" and not (self.follow_up_query and self.follow_up_query.strip()):
            raise ValueError("research requires a follow_up_query")
        return self


T = TypeVar("T", bound=BaseModel)


def parse_model_json(text: object, model: type[T]) -> T:
    """Parse and validate model text. Raises ``ModelOutputError`` on anything unexpected."""
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputError("empty model output")
    if len(text) > _MAX_MODEL_TEXT:
        raise ModelOutputError("model output too large")
    body = text.strip()
    fenced = _FENCE_RE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelOutputError("model output is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ModelOutputError("model output is not a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        where = ".".join(str(p) for p in first["loc"]) or "output"
        raise ModelOutputError(f"schema validation failed at {where}: {first['msg']}") from exc
