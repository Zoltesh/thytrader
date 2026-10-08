"""Operator report models for account balances, portfolios, and fees.

Covers the venue balance report, the portfolios digest with its sleeves, newest
backtest, and paper/live entry-fill comparisons, and the fee tier report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from thytrader.market_data.products import SpotQuoteCurrency
from thytrader.operator.models import OperatorEnvelope, _FrozenModel
from thytrader.portfolios.models import ManagerSettings, PortfolioLimits


class OperatorMoneyPayload(_FrozenModel):
    """Exact money serialized as a decimal string."""

    amount: str
    currency: Literal["USD", "USDC", "USDT"]


class OperatorPortfolioAssetPayload(_FrozenModel):
    """One balance and optional quote valuation without account identifiers."""

    currency: str
    name: str
    available: str
    hold: str
    total: str
    value: OperatorMoneyPayload | None


class PortfolioPayload(_FrozenModel):
    """Point-in-time portfolio matching GET /api/v1/portfolio without secrets."""

    as_of: datetime
    demo: bool
    connection_status: str
    permissions: tuple[str, ...]
    total_value: OperatorMoneyPayload
    assets: tuple[OperatorPortfolioAssetPayload, ...]
    unvalued_assets: tuple[str, ...]

    @field_validator("as_of")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep portfolio timestamps timezone-aware UTC after JSON round-trips."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("as_of must be timezone-aware UTC")
        return value.astimezone(UTC)


class PortfolioReport(OperatorEnvelope):
    """Read-only portfolio snapshot for operators."""

    report_kind: Literal["portfolio"] = "portfolio"
    payload: PortfolioPayload


class PortfolioSleeveDigest(_FrozenModel):
    """One sleeve, its strategy, weight, and what blocks it (ADR 0088)."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    product_id: str | None
    timeframe: str | None
    weight_fraction: str
    issues: tuple[Literal["strategy_invalid", "quote_currency_mismatch", "product_unknown"], ...]


class PortfolioBacktestDigest(_FrozenModel):
    """The newest stored portfolio backtest's headline numbers."""

    result_fingerprint: str
    published_at: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    total_return_fraction: str
    maximum_drawdown_fraction: str
    basket_total_return_fraction: str


class PortfolioDigest(_FrozenModel):
    """One portfolio as operators see it, with its deployment and breaker state (ADR 0091)."""

    portfolio_id: UUID
    name: str
    mode: Literal["paper", "live"]
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    allocated_fraction: str
    unallocated_fraction: str
    revision: int
    sleeves: tuple[PortfolioSleeveDigest, ...]
    largest_asset: str | None
    largest_asset_weight_fraction: str | None
    largest_asset_within_limit: bool | None
    limits: PortfolioLimits
    manager: ManagerSettings
    deployable: bool = False
    deployment_state: Literal[
        "not_deployed", "running", "partially_running", "paused", "stopped"
    ] = "not_deployed"
    breaker_latched: bool = False
    breaker_reason_code: str | None = None
    pending_proposals: int = Field(default=0, ge=0)
    latest_backtest: PortfolioBacktestDigest | None
    active_backtest_jobs: int = Field(ge=0)


class EntryFillDigest(_FrozenModel):
    """One twin's entry-order outcomes (ADR 0097); decimals are canonical strings."""

    deployment_id: UUID
    portfolio_id: UUID | None = None
    strategy_fingerprint: str | None = None
    status: str
    entries_rested: int = Field(ge=0)
    entries_filled: int = Field(ge=0)
    entries_expired: int = Field(ge=0)
    entries_rejected: int = Field(ge=0)
    entries_working: int = Field(ge=0)
    average_fill_vs_limit_bps: str | None = Field(
        default=None, description="Positive is worse than the posted limit; null with no fill."
    )
    average_seconds_to_fill: str | None = None
    median_seconds_to_fill: str | None = None


class PaperLiveFillComparison(_FrozenModel):
    """Explicit twins running verified identical rules, side by side (ADR 0105).

    ``strategy_fingerprint`` references the paper side; each digest names its own snapshot. Paper
    waits run to the fill bar's close (a candle must trade through the limit); live waits
    end at the venue fill.
    """

    strategy_fingerprint: str
    strategy_id: UUID | None = None
    strategy_name: str | None = None
    product_id: str
    paper: EntryFillDigest
    live: EntryFillDigest


class PortfoliosPayload(_FrozenModel):
    """Every portfolio (sleeves, allocation, limits, manager settings, newest backtest).

    ``paper_live_fill_comparisons`` compares the entry fills of explicitly linked paper
    and live books with identical pinned trading rules (sleeves or standalone; ADR 0105).
    """

    portfolio_storage: Literal["available", "unavailable"]
    portfolio_backtest_contract: str
    total: int = Field(ge=0)
    portfolios: tuple[PortfolioDigest, ...] = ()
    paper_live_fill_comparisons: tuple[PaperLiveFillComparison, ...] = ()


class PortfoliosReport(OperatorEnvelope):
    """Read-only portfolio composition report without trading authority."""

    report_kind: Literal["portfolios"] = "portfolios"
    payload: PortfoliosPayload


class FeesPayload(_FrozenModel):
    """Coinbase fee tier plus the research/paper prefill (account rates) and schedule context."""

    taker_fee_rate: str
    maker_fee_rate: str
    usd_volume_30d: str
    fee_tier: str
    as_of: datetime
    source: Literal["coinbase"]
    suggested_maker_fee_rate: str | None = None
    suggested_taker_fee_rate: str | None = None
    suggestion_source: Literal["coinbase_account", "unavailable"]
    suggestion_unavailable_reason: Literal["demo_or_missing_credentials"] | None = None
    suggestion_fee_tier: str | None = None
    suggestion_schedule_tier_id: str | None = None
    suggestion_schedule_version: str | None = None
    suggestion_schedule_as_of: str | None = None
    schedule_maker_fee_rate: str | None = None
    schedule_taker_fee_rate: str | None = None
    suggestion_fetched_at: datetime | None = None

    @field_validator("as_of", "suggestion_fetched_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep fee timestamps timezone-aware UTC after JSON round-trips."""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be timezone-aware UTC")
        return value.astimezone(UTC)


class FeesReport(OperatorEnvelope):
    """Read-only fee profile for operators."""

    report_kind: Literal["fees"] = "fees"
    payload: FeesPayload
