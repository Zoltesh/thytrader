"""The optional ``derivatives`` block of a futures strategy document (ADR 0128).

Present only when ``instrument.kind`` is ``future`` and omitted otherwise, so spot documents
keep their canonical bytes and fingerprints.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from thytrader.strategies.schema.primitives import DecimalText, _FrozenModel

MAX_STRATEGY_LEVERAGE = Decimal(20)


class Derivatives(_FrozenModel):
    """Futures settings a strategy carries.

    ``max_leverage`` is the strategy's own ceiling (gross notional at mark over futures
    equity); the risk policy's ceiling applies too and the lower wins. ``margin_mode`` is
    ``overnight`` in P1: intraday rates are never used for sizing or admission.
    ``flatten_before_expiry_hours`` flattens a dated contract that many hours before its
    expiry; it is required for dated contracts when the contract is bound.
    """

    max_leverage: DecimalText
    margin_mode: Literal["overnight"] = "overnight"
    flatten_before_expiry_hours: int | None = Field(
        default=None, ge=1, le=720, exclude_if=lambda value: value is None
    )

    @field_validator("max_leverage")
    @classmethod
    def require_bounded_leverage(cls, value: str) -> str:
        """Leverage is at least 1 (unlevered) and at most 20."""
        leverage = Decimal(value)
        if leverage < 1 or leverage > MAX_STRATEGY_LEVERAGE:
            raise ValueError("derivatives.max_leverage must be between 1 and 20")
        return value
