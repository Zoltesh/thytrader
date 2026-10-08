"""Validated portfolio value types: canonical decimal text, fractions, amounts, and text.

Money and fractions travel as canonical plain decimal strings, exactly like the research
contracts: weights and fractions allow at most four decimal places (0.01%), quote amounts
at most eight. Names, notes, and mandates are trimmed and refuse control characters. The
``Annotated`` aliases here are the Pydantic field types of the portfolio commands.
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Annotated
import unicodedata

from pydantic import AfterValidator, Field, StrictInt

from thytrader.portfolios.vocabulary import (
    FRACTION_PLACES,
    MAX_CAPITAL_QUOTE,
    MAX_MANDATE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_NOTE_LENGTH,
    QUOTE_PLACES,
)

_DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_DECIMAL_TEXT_LENGTH = 40
_ZERO = Decimal(0)
_ONE = Decimal(1)
_ALLOWED_MANDATE_CONTROLS = frozenset({"\n", "\t"})


def canonical_decimal_text(value: str, *, places: int, label: str) -> str:
    """Validate one non-negative plain decimal string and return its canonical spelling."""
    if len(value) > _MAX_DECIMAL_TEXT_LENGTH or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError(f"{label} must be a non-negative plain decimal string such as '0.25'")
    whole, _separator, fraction = value.partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) > places:
        raise ValueError(f"{label} allows at most {places} decimal places")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{fraction}" if fraction else whole


def _ranged(
    value: str,
    *,
    label: str,
    places: int,
    minimum: Decimal,
    maximum: Decimal,
    minimum_inclusive: bool,
    maximum_inclusive: bool,
) -> str:
    """Canonicalize one decimal and require it inside a documented interval."""
    text = canonical_decimal_text(value, places=places, label=label)
    amount = Decimal(text)
    above = amount >= minimum if minimum_inclusive else amount > minimum
    below = amount <= maximum if maximum_inclusive else amount < maximum
    if not (above and below):
        left = "[" if minimum_inclusive else "("
        right = "]" if maximum_inclusive else ")"
        raise ValueError(f"{label} must be in {left}{minimum}, {maximum}{right}")
    return text


def _weight_fraction(value: str) -> str:
    """A sleeve weight: more than 0 and at most 1."""
    return _ranged(
        value,
        label="weight_fraction",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _reserve_fraction(value: str) -> str:
    """The cash reserve: at least 0 and below 1."""
    return _ranged(
        value,
        label="cash_reserve_fraction",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=True,
        maximum_inclusive=False,
    )


def _limit_fraction(value: str) -> str:
    """An exposure cap: more than 0 and at most 1 of portfolio capital."""
    return _ranged(
        value,
        label="exposure limit",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _drawdown_fraction(value: str) -> str:
    """A drawdown stop: more than 0 and below 1."""
    return _ranged(
        value,
        label="max_drawdown_fraction",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=False,
    )


def _weekly_change_fraction(value: str) -> str:
    """The manager's weekly weight-change budget: more than 0 and at most 1."""
    return _ranged(
        value,
        label="max_weight_change_per_week",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _quote_amount(value: str) -> str:
    """A positive quote amount up to 1e15 with at most eight decimal places."""
    return _ranged(
        value,
        label="quote amount",
        places=QUOTE_PLACES,
        minimum=_ZERO,
        maximum=MAX_CAPITAL_QUOTE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _has_disallowed_controls(value: str, *, allowed: frozenset[str] = frozenset()) -> bool:
    """True when text contains control or format characters outside ``allowed``."""
    return any(
        unicodedata.category(character).startswith("C") and character not in allowed
        for character in value
    )


def _portfolio_name(value: str) -> str:
    """Trim a portfolio name and require 1-120 visible characters."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("name must not be blank")
    if len(stripped) > MAX_NAME_LENGTH:
        raise ValueError(f"name allows at most {MAX_NAME_LENGTH} characters")
    if _has_disallowed_controls(stripped):
        raise ValueError("name must not contain control characters")
    return stripped


def _sleeve_note(value: str) -> str:
    """Trim a sleeve note; an empty note means "no note"."""
    stripped = value.strip()
    if len(stripped) > MAX_NOTE_LENGTH:
        raise ValueError(f"note allows at most {MAX_NOTE_LENGTH} characters")
    if _has_disallowed_controls(stripped):
        raise ValueError("note must be one line without control characters")
    return stripped


def _mandate(value: str) -> str:
    """Trim a manager mandate (multi-line plain text, at most 2000 characters)."""
    stripped = value.strip()
    if len(stripped) > MAX_MANDATE_LENGTH:
        raise ValueError(f"mandate allows at most {MAX_MANDATE_LENGTH} characters")
    if _has_disallowed_controls(stripped, allowed=_ALLOWED_MANDATE_CONTROLS):
        raise ValueError("mandate must be plain text")
    return stripped


WeightFractionText = Annotated[str, Field(strict=True), AfterValidator(_weight_fraction)]
ReserveFractionText = Annotated[str, Field(strict=True), AfterValidator(_reserve_fraction)]
LimitFractionText = Annotated[str, Field(strict=True), AfterValidator(_limit_fraction)]
DrawdownFractionText = Annotated[str, Field(strict=True), AfterValidator(_drawdown_fraction)]
WeeklyChangeFractionText = Annotated[
    str, Field(strict=True), AfterValidator(_weekly_change_fraction)
]
QuoteAmountText = Annotated[str, Field(strict=True), AfterValidator(_quote_amount)]
PortfolioNameText = Annotated[str, Field(strict=True), AfterValidator(_portfolio_name)]
SleeveNoteText = Annotated[str, Field(strict=True), AfterValidator(_sleeve_note)]
MandateText = Annotated[str, Field(strict=True), AfterValidator(_mandate)]
RevisionNumber = Annotated[StrictInt, Field(ge=1)]
