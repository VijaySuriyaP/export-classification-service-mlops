"""Request data contract v1 for ``POST /runs``.

Validation layers enforced here, before any agent run is created (D1 / F2):

* schema   - unknown fields are rejected (``extra="forbid"``), required field present
* type     - every field has a declared type; no coercion of objects into strings
* range    - string lengths, list sizes and quantity bounds
* category - ``chapter_hint`` and ``quantity_unit`` are closed enumerations
* cross-field - ``hs_code_hint`` must belong to ``chapter_hint``;
                ``quantity`` and ``quantity_unit`` must be supplied together
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

SUPPORTED_CHAPTERS: tuple[str, ...] = ("73", "96")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MATERIAL = re.compile(r"^[A-Za-z][A-Za-z0-9 ,.'()/\-]{1,49}$")


class RunRequest(BaseModel):
    """A product to classify. Only ``product_description`` is required."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        strict=False,
        json_schema_extra={
            "examples": [
                {"product_description": "Stainless steel vacuum flask with a moulded plastic outer body."},
                {
                    "product_description": "Stainless steel vacuum flask with a moulded plastic outer body.",
                    "materials": ["stainless steel", "plastic"],
                    "destination_country": "AE",
                    "quantity": 500,
                    "quantity_unit": "pcs",
                    "client_reference": "PO-2026-0042",
                },
            ]
        },
    )

    contract_version: Literal["1"] = Field(default="1", description="Data contract version.")
    product_description: str = Field(
        min_length=10,
        max_length=2000,
        description="Free-text description of the product as it would appear on the invoice.",
    )
    materials: list[str] | None = Field(
        default=None,
        max_length=10,
        description="Optional list of constituent materials (max 10, each 2-50 characters).",
    )
    chapter_hint: Literal["73", "96"] | None = Field(
        default=None, description="Optional chapter the submitter believes applies. Advisory only."
    )
    hs_code_hint: str | None = Field(
        default=None,
        pattern=r"^\d{4}(\d{2}){0,2}$",
        description="Optional 4, 6 or 8 digit code the submitter believes applies. Advisory only.",
    )
    destination_country: str | None = Field(
        default=None, pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alpha-2 destination country code."
    )
    quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    quantity_unit: Literal["pcs", "kg", "set", "pair", "dozen"] | None = None
    client_reference: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description="Caller's own reference, echoed back. Never sent to a model.",
    )

    @field_validator("product_description")
    @classmethod
    def _check_description(cls, value: str) -> str:
        if _CONTROL_CHARS.search(value):
            raise PydanticCustomError("control_characters", "must not contain control characters")
        if sum(ch.isalpha() for ch in value) < 5:
            raise PydanticCustomError(
                "not_descriptive", "must contain at least 5 letters describing the product"
            )
        return value

    @field_validator("materials")
    @classmethod
    def _check_materials(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned: list[str] = []
        for item in value:
            item = item.strip()
            if not _MATERIAL.match(item):
                raise PydanticCustomError(
                    "invalid_material",
                    "each material must be 2-50 characters, start with a letter and use only "
                    "letters, digits, spaces or basic punctuation",
                )
            if item.lower() not in (c.lower() for c in cleaned):
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _cross_field(self) -> "RunRequest":
        if self.hs_code_hint and self.chapter_hint and not self.hs_code_hint.startswith(self.chapter_hint):
            raise PydanticCustomError(
                "cross_field",
                "hs_code_hint {hs} does not belong to chapter_hint {ch}",
                {"fields": ["hs_code_hint", "chapter_hint"], "hs": self.hs_code_hint, "ch": self.chapter_hint},
            )
        if (self.quantity is None) != (self.quantity_unit is None):
            raise PydanticCustomError(
                "cross_field",
                "quantity and quantity_unit must be provided together",
                {"fields": ["quantity", "quantity_unit"]},
            )
        return self

    def model_text_fields(self) -> dict[str, str]:
        """Free-text fields that may reach a model (subject to prompt-injection screening, L4)."""
        fields = {"product_description": self.product_description}
        if self.materials:
            fields["materials"] = ", ".join(self.materials)
        return fields
