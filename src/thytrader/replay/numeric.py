"""Shared replay financial precision validation, independent of ambient Decimal context."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import (
    ROUND_HALF_EVEN,
    Clamped,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Subnormal,
    Underflow,
    localcontext,
)
import re
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

PRECISION = 64
_UNSAFE_SIGNALS = (Clamped, DivisionByZero, InvalidOperation, Overflow, Subnormal, Underflow)
MIN_ADJUSTED = -128
MAX_ADJUSTED = 128
MIN_EXPONENT = MIN_ADJUSTED - PRECISION + 1
MAX_TEXT_LENGTH = 196
_DECIMAL_LEXEME = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@contextmanager
def financial_arithmetic(*, precision: int | None = None) -> Iterator[None]:
    """Keep ordinary rounding, but refuse unsafe arithmetic regardless of ambient traps.

    Nonzero subnormals are outside the published input envelope even when exact.
    Check flags as well as traps so disabled traps cannot certify finite saturation.
    """
    with localcontext() as context:
        if precision is not None:
            context.prec = precision
        context.rounding = ROUND_HALF_EVEN
        context.Emin = MIN_ADJUSTED
        context.Emax = MAX_ADJUSTED
        context.clamp = 0
        for signal in context.traps:
            context.traps[signal] = signal in _UNSAFE_SIGNALS
        context.clear_flags()
        try:
            yield
            if any(context.flags[signal] for signal in _UNSAFE_SIGNALS):
                raise ValueError("replay arithmetic exceeds the financial envelope")
        except DecimalException as error:
            raise ValueError("replay arithmetic exceeds the financial envelope") from error


def validate_decimal(value: Decimal) -> Decimal:
    """Reject unusable magnitude/precision before context-free fixed-point rendering."""
    if not value.is_finite() or not MIN_ADJUSTED <= value.adjusted() <= MAX_ADJUSTED:
        raise ValueError("replay financial evidence exceeds the finite Decimal envelope")
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int) or not MIN_EXPONENT <= parts.exponent <= MAX_ADJUSTED:
        raise ValueError("replay financial evidence exceeds the finite Decimal envelope")
    digits = parts.digits
    size = len(digits)
    if size > MAX_TEXT_LENGTH:
        raise ValueError("replay financial coefficient exceeds the lexical budget")
    while size > 1 and digits[size - 1] == 0:
        size -= 1
    if size > PRECISION:
        raise ValueError("replay financial evidence exceeds the finite Decimal envelope")
    return value


def financial_text(value: str) -> Decimal:
    """Bound parsing work before constructing a Decimal from financial text."""
    if len(value) > MAX_TEXT_LENGTH:
        raise ValueError("replay financial text exceeds the lexical budget")
    try:
        return validate_decimal(Decimal(value))
    except DecimalException as error:
        raise ValueError("replay financial text exceeds the finite Decimal envelope") from error


def validate_numeric_inputs(value: object) -> None:
    """Bound raw numeric lexemes before nested domain adapters can allocate Decimals.

    This walk is confined to normalized model input. Numeric-only string identifiers
    share the lexical budget; other strings are never parsed as financial values.
    """
    if isinstance(value, str):
        if value and value[0] in "+-.0123456789" and _DECIMAL_LEXEME.fullmatch(value):
            financial_text(value)
    elif isinstance(value, Decimal):
        validate_decimal(value)
    elif isinstance(value, dict):
        for item in cast("dict[str, object]", value).values():
            validate_numeric_inputs(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_numeric_inputs(item)
