"""Typed payload models of the operator ``readiness`` report (ADR 0114)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.exchanges.read_errors import ExchangeReadFailure
from thytrader.market_data.products import SpotQuoteCurrency
from thytrader.operator.models import OperatorEnvelope
from thytrader.portfolios.models import PortfolioLimits

READINESS_NOTE = (
    "Advisory only: this report never tightens or changes the published risk policy, "
    "allocations, or bot state. The entry gate enforces caps at order time."
)


FEE_COMPARISON_NOTE = (
    "Paper books carry documented maker/taker assumptions; live books pay venue-recorded "
    "fees. Backtest and paper suggestions prefill from the account's reported rates; older "
    "runs may assume cheaper fees. Compare with `thytrader-operator fees`."
)


class ReadinessSeverity(StrEnum):
    """How hard one readiness finding should press on the operator."""

    INFO = "info"
    ADVISORY = "advisory"
    VIOLATION = "violation"
    UNKNOWN = "unknown"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime | None) -> datetime | None:
    """Keep report timestamps timezone-aware UTC after JSON round-trips."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("readiness timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


class ReadinessFinding(_FrozenModel):
    """One advisory, violation, or unknown-capacity observation with a stable code."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    severity: ReadinessSeverity
    deployment_id: UUID | None = None
    portfolio_id: UUID | None = None
    detail: str = Field(min_length=1, max_length=500)


class ReadinessVenueQuote(_FrozenModel):
    """One quote currency's venue balance, exactly as observed.

    Balances are disclosed (``balances_omitted=false``) because the report exists to
    compare them against caps. Account identifiers and secrets stay out.
    """

    quote_currency: SpotQuoteCurrency
    venue_available: str | None = None
    venue_hold: str | None = None
    venue_total: str | None = None
    observed_at: datetime | None = None
    demo: bool = False

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep venue observation timestamps timezone-aware UTC."""
        return _require_utc(value)


class ReadinessInventoryEvidence(_FrozenModel):
    """Storage-read completeness and economic completeness are independent evidence."""

    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    expected_books: int | None = Field(default=None, ge=0)
    read_books: int = Field(default=0, ge=0)
    missing_deployment_ids: tuple[UUID, ...] = ()
    unsupported_products: tuple[str, ...] = ()
    unpriced_entry_order_ids: tuple[UUID, ...] = ()
    accounting_status: Literal["complete", "unresolved", "unavailable"] = "unavailable"
    unresolved_deployment_ids: tuple[UUID, ...] = ()


class ReadinessQuoteExposure(_FrozenModel):
    """One book's cost-basis exposure in exactly one quote currency, without FX."""

    quote_currency: SpotQuoteCurrency
    inventory_cost: str | None
    working_entry_reserved: str
    exposure: str | None


class ReadinessProductCapRow(_FrozenModel):
    """Account-wide cost-basis exposure on one product versus the per-product cap."""

    product_id: str
    quote_currency: SpotQuoteCurrency | None = None
    exposure: str | None
    cap: str | None = None
    remaining: str | None = None


class ReadinessAccountCaps(_FrozenModel):
    """Account-level capacity in the policy's quote currency (ADR 0106 scope).

    ``capital_base`` is observed venue available quote plus managed long inventory cost
    and working buy-entry reservations — never a bot allocation or duplicated ledger
    cash. Caps and capacities are ``None`` when the venue balance is unknown; they are
    never guessed. Books quoted in another currency are excluded and disclosed.
    """

    quote_currency: SpotQuoteCurrency
    policy_source: Literal["compiled_default", "published"]
    policy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    enforcement: Literal["advisory_only"] = "advisory_only"
    capital_base: str | None = None
    venue_available_quote: str | None = None
    inventory: ReadinessInventoryEvidence
    excluded_products: tuple[str, ...] = ()
    exposure_basis: Literal["position_cost_plus_working_entries"] = (
        "position_cost_plus_working_entries"
    )
    managed_long_inventory_cost: str | None = None
    working_buy_entry_reserved: str | None = None
    current_exposure: str | None = None
    exposure_fraction: str
    absolute_exposure_cap: str | None = None
    effective_exposure_cap: str | None = None
    remaining_entry_capacity: str | None = None
    per_product_fraction: str
    product_caps: tuple[ReadinessProductCapRow, ...] = ()
    daily_loss_fraction: str
    daily_loss_quote_cap: str | None = None
    effective_daily_loss_cap: str | None = None
    drawdown_fraction: str
    venue_read_failure: ExchangeReadFailure | None = None


class ReadinessDeploymentRow(_FrozenModel):
    """One in-scope book's allocation and capacity facts (no order payloads)."""

    deployment_id: UUID
    mode: Literal["paper", "live"]
    status: str
    kind: str
    strategy_name: str | None = None
    product_id: str
    quote_currency: SpotQuoteCurrency | None = None
    portfolio_id: UUID | None = None
    allocated_capital: str | None = None
    allocation_basis: Literal["stored", "portfolio_weight", "paper_starting_cash", "none"] = "none"
    quote_exposures: tuple[ReadinessQuoteExposure, ...] = ()
    unsupported_products: tuple[str, ...] = ()
    unpriced_entry_order_ids: tuple[UUID, ...] = ()
    inventory_cost: str | None = None
    working_entry_reserved: str | None = None
    exposure: str | None = None
    allocation_remaining: str | None = None
    daily_loss_latched: bool = False
    drawdown_latched: bool = False
    paper_maker_fee_rate: str | None = None
    paper_taker_fee_rate: str | None = None


class ReadinessAssetCapRow(_FrozenModel):
    """One base asset's exposure inside one portfolio versus its per-asset cap."""

    asset: str
    exposure: str
    cap: str
    remaining: str


class ReadinessPortfolioSection(_FrozenModel):
    """One portfolio's caps, exposure, and both breaker tiers side by side.

    ``tighter_daily_breaker`` only *names* which daily-loss stop binds first; nothing
    here tightens either breaker. Exposure caps are fractions of the portfolio's
    configured ``capital_quote`` exactly as the entry gate applies them (ADR 0091).
    """

    portfolio_id: UUID
    name: str
    mode: Literal["paper", "live"]
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    allocated_quote: str
    limits: PortfolioLimits
    total_exposure_cap: str
    per_asset_cap: str
    inventory: ReadinessInventoryEvidence
    excluded_products: tuple[str, ...] = ()
    current_total_exposure: str | None = None
    remaining_total_capacity: str | None = None
    asset_caps: tuple[ReadinessAssetCapRow, ...] = ()
    runtime_available: bool = False
    breaker_latched: bool | None = None
    breaker_reason_code: str | None = None
    daily_loss_quote_stop: str | None = None
    drawdown_fraction_stop: str | None = None
    drawdown_stop_loss_allowance: str | None = None
    account_daily_loss_cap: str | None = None
    account_breaker_comparable: bool = False
    tighter_daily_breaker: Literal[
        "portfolio", "account", "neither_set", "unknown", "not_comparable"
    ] = "unknown"


class ReadinessFeeGapRow(_FrozenModel):
    """One paper book's assumed fees versus the account's reported fee evidence.

    A gap is ``account rate - assumed rate``: positive means the paper book assumes
    cheaper fees than the account reports, so its results read more optimistic.
    """

    deployment_id: UUID
    product_id: str
    assumed_maker_fee_rate: str
    assumed_taker_fee_rate: str
    account_maker_fee_rate: str | None = None
    account_taker_fee_rate: str | None = None
    maker_gap: str | None = None
    taker_gap: str | None = None
    more_optimistic: bool = False


class ReadinessFeeEvidence(_FrozenModel):
    """Account fee evidence and every scoped paper book's assumption against it.

    ``unavailable_reason`` is set when the account rates could not be read; assumed
    paper rates are still listed, but no optimism conclusion is drawn from invented
    numbers. Demo data is labeled and never compared against real expectations.
    """

    account_maker_fee_rate: str | None = None
    account_taker_fee_rate: str | None = None
    account_fee_tier: str | None = None
    account_as_of: datetime | None = None
    demo: bool = False
    unavailable_reason: Literal["demo_or_missing_credentials", "read_failure"] | None = None
    read_failure: ExchangeReadFailure | None = None
    paper_books_compared: int = Field(default=0, ge=0)
    paper_books_defaulting_rates: int = Field(default=0, ge=0)
    optimistic_books: tuple[ReadinessFeeGapRow, ...] = ()
    comparison_note: str = FEE_COMPARISON_NOTE

    @field_validator("account_as_of")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep fee-evidence timestamps timezone-aware UTC."""
        return _require_utc(value)


class ReadinessPaperSection(_FrozenModel):
    """Paper-book capacity in the policy quote currency (advisory, rehearsal money)."""

    quote_currency: SpotQuoteCurrency
    paper_capital_quote: str
    committed_starting_cash: str
    books: int = Field(ge=0)


class ReadinessFuturesPosition(_FrozenModel):
    """One external CFM position: unmanaged, never part of any book."""

    product_id: str
    side: Literal["long", "short", "unknown"]
    number_of_contracts: str


class ReadinessFuturesSection(_FrozenModel):
    """The CFM futures account from the newest mirror snapshot (ADR 0127).

    Every amount is USD and is never added to a USDC amount. Coinbase counts the USDC
    spot balance as futures collateral, so ``futures_buying_power`` is shared with USDC
    spot capital (``collateral_note``). ``positions`` is ``null`` when unknown.
    """

    observed_at: datetime | None
    stale: bool
    enablement: Literal["enabled", "not_enabled", "unknown"]
    currency: Literal["USD"] = "USD"
    futures_buying_power: str | None = None
    cbi_usd_balance: str | None = None
    cfm_usd_balance: str | None = None
    available_margin: str | None = None
    liquidation_threshold: str | None = None
    liquidation_buffer_amount: str | None = None
    liquidation_buffer_percentage: str | None = None
    margin_ratio: str | None = None
    unrealized_pnl: str | None = None
    funding_pnl: str | None = None
    positions: tuple[ReadinessFuturesPosition, ...] | None = None
    collateral_note: str

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep the snapshot instant UTC."""
        return _require_utc(value)


class ReadinessPayload(_FrozenModel):
    """Everything the preflight compares, with its scope made explicit."""

    scope: Literal["deployment", "portfolio", "fleet"]
    deployment_id: UUID | None = None
    portfolio_id: UUID | None = None
    modes_in_scope: tuple[Literal["paper", "live"], ...] = ()
    inventory: ReadinessInventoryEvidence = Field(default_factory=ReadinessInventoryEvidence)
    portfolio_scope_complete: bool = False
    note: str = READINESS_NOTE
    venue_quotes: tuple[ReadinessVenueQuote, ...] = ()
    account: ReadinessAccountCaps | None = None
    paper: ReadinessPaperSection | None = None
    deployments: tuple[ReadinessDeploymentRow, ...] = ()
    portfolios: tuple[ReadinessPortfolioSection, ...] = ()
    fee_evidence: ReadinessFeeEvidence = Field(default_factory=ReadinessFeeEvidence)
    futures: ReadinessFuturesSection | None = None
    findings: tuple[ReadinessFinding, ...] = ()


class ReadinessReport(OperatorEnvelope):
    """Advisory readiness preflight; never mutates policy, allocations, or bots."""

    report_kind: Literal["readiness"] = "readiness"
    payload: ReadinessPayload
