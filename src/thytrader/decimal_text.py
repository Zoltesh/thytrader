"""Canonical text for exact Decimal values shared across packages.

This module imports nothing from ThyTrader, so market data, execution, operator,
portfolio, and backtest code can render Decimals without depending on research.
``thytrader.evaluation.indicators`` re-exports ``canonical_decimal``.
"""

from decimal import (
    Clamped,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Underflow,
    localcontext,
)


def display_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """Four-place display only; contain rounding, overflow and underflow without caller flags.

    Safety decisions must compare exact products rather than this rounded presentation.
    A nonzero value rounded to zero stays unknown, never a fabricated zero ratio.
    """
    if (
        numerator is None
        or denominator is None
        or not numerator.is_finite()
        or not denominator.is_finite()
        or denominator <= 0
    ):
        return None
    try:
        with localcontext() as context:
            for signal in (Clamped, InvalidOperation, Overflow, Underflow):
                context.traps[signal] = True
            context.traps[Inexact] = False
            context.traps[Rounded] = False
            ratio = (numerator / denominator).quantize(Decimal("0.0001"))
            if not ratio.is_finite() or (ratio == 0 and numerator != 0):
                return None
            return ratio
    except DecimalException:
        return None


def canonical_decimal(value: Decimal) -> str:
    """Render one finite engine Decimal without exponent notation or trailing zeros."""
    text = format(value, "f")
    whole, separator, fraction = text.partition(".")
    canonical_fraction = fraction.rstrip("0") if separator else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    result = f"{whole}{decimal_places}"
    return "0" if Decimal(result).is_zero() else result
