"""The optional ``futures`` block of the risk policy (ADR 0129).

P1-2a ships the two fields of the live spot collateral gate (ADR 0129 §2 L3). P1-4 adds the
paper futures envelope and the futures-scope daily loss limit (§4, §7); P1-5 adds the rest
of the futures entry gate. The block is excluded from the canonical policy document while
unset, and each optional field while unset, so every existing policy keeps its fingerprint.
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

    ``paper_capital_usd`` is the simulated USD envelope paper futures books draw from; it is
    separate from ``paper_capital_quote`` and an unset value refuses a paper futures start.
    ``daily_loss_limit_fraction`` bounds the futures-scope daily loss (change in paper futures
    book equity since the UTC day open) as a fraction of ``paper_capital_usd``; unset means
    the policy's ``daily_loss_limit_fraction`` applies to that USD capital.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    live_spot_collateral_reserve_quote: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    peg_haircut: DecimalText = DEFAULT_PEG_HAIRCUT
    paper_capital_usd: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    daily_loss_limit_fraction: DecimalText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("live_spot_collateral_reserve_quote", "paper_capital_usd")
    @classmethod
    def require_positive_amount(cls, value: str | None) -> str | None:
        """A reserve or envelope, when set, is a positive amount."""
        if value is not None and Decimal(value) <= 0:
            raise ValueError("futures amounts must be greater than 0")
        return value

    @field_validator("daily_loss_limit_fraction")
    @classmethod
    def require_unit_fraction(cls, value: str | None) -> str | None:
        """A daily loss fraction lies in (0, 1]."""
        if value is not None and not Decimal(0) < Decimal(value) <= 1:
            raise ValueError("futures daily_loss_limit_fraction must be in (0, 1]")
        return value

    @field_validator("peg_haircut")
    @classmethod
    def require_haircut_at_least_one(cls, value: str) -> str:
        """A haircut below 1.0 would let a smaller USDC reserve cover larger USD margin."""
        if Decimal(value) < 1:
            raise ValueError("peg_haircut must be at least 1.0")
        return value
