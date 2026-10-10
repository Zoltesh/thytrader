"""The optional ``futures`` block of the risk policy (ADR 0129).

P1-2a ships the two fields of the live spot collateral gate (ADR 0129 §2 L3). Later P1
slices add the futures entry gate fields to this same block. The block is excluded from the
canonical policy document while unset, so every existing policy keeps its fingerprint.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.strategies.models import DecimalText

DEFAULT_PEG_HAIRCUT = "1.25"


class FuturesRiskPolicy(BaseModel):
    """Futures-related risk settings.

    ``live_spot_collateral_reserve_quote`` is an amount in the policy quote that live spot
    books leave untouched for futures margin. While manual futures are ``in_use``, live
    USDC/USD spot entries continue only when it is set, it covers the CFM initial margin
    times ``peg_haircut`` (a yes/no threshold check; the two currencies are never added),
    and it is withheld from the spot capital base. ``peg_haircut`` is ≥ 1.0.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    live_spot_collateral_reserve_quote: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    peg_haircut: DecimalText = DEFAULT_PEG_HAIRCUT

    @field_validator("live_spot_collateral_reserve_quote")
    @classmethod
    def require_positive_reserve(cls, value: str | None) -> str | None:
        """A reserve, when set, is a positive amount."""
        if value is not None and Decimal(value) <= 0:
            raise ValueError("live_spot_collateral_reserve_quote must be greater than 0")
        return value

    @field_validator("peg_haircut")
    @classmethod
    def require_haircut_at_least_one(cls, value: str) -> str:
        """A haircut below 1.0 would let a smaller USDC reserve cover larger USD margin."""
        if Decimal(value) < 1:
            raise ValueError("peg_haircut must be at least 1.0")
        return value
