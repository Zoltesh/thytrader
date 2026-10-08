"""Canonical text for exact Decimal values shared across packages.

This module imports nothing from ThyTrader, so market data, execution, operator,
portfolio, and backtest code can render Decimals without depending on research.
``thytrader.research.indicators`` re-exports ``canonical_decimal``.
"""

from decimal import Decimal


def canonical_decimal(value: Decimal) -> str:
    """Render one finite engine Decimal without exponent notation or trailing zeros."""
    text = format(value, "f")
    whole, separator, fraction = text.partition(".")
    canonical_fraction = fraction.rstrip("0") if separator else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    result = f"{whole}{decimal_places}"
    return "0" if Decimal(result).is_zero() else result
