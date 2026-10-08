"""Shared validated scalar types and the frozen base model of the strategy schema."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _decimal_text(value: str) -> str:
    """Validate and normalize one finite plain decimal string without numeric coercion."""
    if len(value) > 64 or not _DECIMAL_TEXT_PATTERN.fullmatch(value):
        raise ValueError("financial values must be plain decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("financial values must be valid decimal strings") from error
    if not parsed.is_finite():
        raise ValueError("financial values must be finite")
    unsigned = value.removeprefix("-")
    whole, separator, fraction = unsigned.partition(".")
    canonical_whole = whole.lstrip("0") or "0"
    canonical_fraction = fraction.rstrip("0") if separator else ""
    if canonical_whole == "0" and not canonical_fraction:
        return "0"
    sign = "-" if value.startswith("-") else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    return f"{sign}{canonical_whole}{decimal_places}"


_DECIMAL_TEXT_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_UUID7_TEXT_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

DecimalText = Annotated[str, Field(strict=True), AfterValidator(_decimal_text)]


def _require_uuid7(value: UUID) -> UUID:
    """Reject identifiers that are not time-sortable UUID version 7."""
    if value.version != 7:
        raise ValueError("must be UUIDv7")
    return value


Uuid7 = Annotated[
    UUID,
    AfterValidator(_require_uuid7),
    Field(
        description="Time-sortable UUID version 7 strategy identity.",
        json_schema_extra={"format": "uuid7", "pattern": _UUID7_TEXT_PATTERN.pattern},
    ),
]


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
