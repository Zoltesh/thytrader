"""Research and paper maker/taker prefill from the account's own Coinbase fee tier.

The suggestion is the account's reported ``maker_fee_rate`` / ``taker_fee_rate`` from the
Coinbase ``transaction_summary``, which is what live fills are billed at (ADR 0090). The
tier ladder (current and next tier, how the account qualifies) is reported by Coinbase in
the same response and travels on ``FeeProfile`` (ADR 0144); ThyTrader pins no published
schedule. Demo and missing credentials suggest nothing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile


class ResearchFeeSuggestion(BaseModel):
    """Research/paper maker/taker prefill derived from a live fee-tier snapshot.

    ``suggested_*`` are the account's reported Coinbase rates (``source`` is
    ``coinbase_account``). Suggested rates are modeled CostAssumptions defaults.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    suggested_maker_fee_rate: Decimal | None = None
    suggested_taker_fee_rate: Decimal | None = None
    source: Literal["coinbase_account", "unavailable"]
    unavailable_reason: Literal["demo_or_missing_credentials"] | None = None
    fee_tier: str | None = Field(default=None, min_length=1, max_length=64)
    fetched_at: datetime | None = None

    @model_validator(mode="after")
    def require_source_consistent_fields(self) -> Self:
        """Keep unavailable suggestions empty and account suggestions fully identified."""
        if self.source == "unavailable":
            if self.unavailable_reason is None:
                raise ValueError("unavailable suggestions require a reason")
            if any(
                value is not None
                for value in (
                    self.suggested_maker_fee_rate,
                    self.suggested_taker_fee_rate,
                    self.fee_tier,
                    self.fetched_at,
                )
            ):
                raise ValueError("unavailable suggestions must not invent account rates")
            return self
        if self.unavailable_reason is not None:
            raise ValueError("account suggestions must not carry an unavailable reason")
        if (
            self.suggested_maker_fee_rate is None
            or self.suggested_taker_fee_rate is None
            or self.fee_tier is None
            or self.fetched_at is None
        ):
            raise ValueError("account suggestions require rates, tier and fetch time")
        return self


def suggest_research_fee_rates(*, profile: FeeProfile, demo: bool) -> ResearchFeeSuggestion:
    """Suggest the account's reported rates, or none in demo.

    The account's ``transaction_summary`` rates are what Coinbase bills live fills at, so
    they are the honest research and paper default (ADR 0090).
    """
    if demo:
        return ResearchFeeSuggestion(
            source="unavailable",
            unavailable_reason="demo_or_missing_credentials",
        )
    return ResearchFeeSuggestion(
        suggested_maker_fee_rate=profile.maker_fee_rate,
        suggested_taker_fee_rate=profile.taker_fee_rate,
        source="coinbase_account",
        fee_tier=profile.fee_tier,
        fetched_at=profile.as_of,
    )
