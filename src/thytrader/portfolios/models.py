"""Portfolio domain records, settings, journal entries, and commands (ADR 0088).

A portfolio's ``mode`` and ``quote_currency`` are fixed at creation. The manager settings
bound what the manager agent's proposals may change without approval (ADR 0091); no field
can grant order authority, because strategies place every trade. The validated value types
live in ``portfolios.values``, the shared literals and bounds in ``portfolios.vocabulary``,
and the failures in ``portfolios.errors``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.market_data.products import (
    SpotQuoteCurrency,
    base_currency,
    is_spot_product_id,
    quote_currency,
)
from thytrader.portfolios.errors import PortfolioSleeveNotFoundError
from thytrader.portfolios.values import (
    DrawdownFractionText,
    LimitFractionText,
    MandateText,
    PortfolioNameText,
    QuoteAmountText,
    ReserveFractionText,
    RevisionNumber,
    SleeveNoteText,
    WeeklyChangeFractionText,
    WeightFractionText,
)
from thytrader.portfolios.vocabulary import (
    MANAGER_PERMISSION_KEYS,
    MAX_NAME_LENGTH,
    MAX_RATIONALE_LENGTH,
    MAX_SLEEVES,
    MAX_SUMMARY_LENGTH,
    BreakerReason,
    JournalActor,
    JournalChannel,
    JournalKind,
    JournalReason,
    PortfolioMode,
    SleeveIssueCode,
)
from thytrader.strategies.models import covered_product_ids

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    from pydantic import JsonValue

    from thytrader.strategies.library import StrategyRecord


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PortfolioLimits(_FrozenModel):
    """Shared limits stored on a portfolio (fractions of portfolio capital).

    On a deployed portfolio the exposure caps bind every sleeve's new entries in the risk
    gate, and the optional daily-loss and drawdown stops are portfolio breakers that pause
    every sleeve and latch until an operator reset (ADR 0091). Portfolio backtests do not
    simulate them.
    """

    max_total_exposure_fraction: LimitFractionText = Field(
        default="1", description="Cap on all sleeves' combined position value."
    )
    max_per_asset_fraction: LimitFractionText = Field(
        default="1", description="Cap on position value in any one base asset."
    )
    daily_loss_quote: QuoteAmountText | None = Field(
        default=None, description="Optional UTC-day loss stop in the quote currency."
    )
    max_drawdown_fraction: DrawdownFractionText | None = Field(
        default=None, description="Optional stop on drawdown from the portfolio's peak."
    )


class ManagerPermissions(_FrozenModel):
    """What the manager agent's proposals may do without approval. It never places orders.

    ``may_rebalance`` auto-applies rebalances on a paper portfolio within the rolling
    7-day ``max_weight_change_per_week`` budget (a live rebalance always waits for
    approval); ``may_pause_sleeves`` auto-applies pausing a running sleeve;
    ``may_propose_sleeves`` lets the manager propose new sleeves, which always need
    approval. Strategies place every trade (ADR 0091).
    """

    may_rebalance: StrictBool = False
    max_weight_change_per_week: WeeklyChangeFractionText = Field(
        default="0.1",
        description="Largest total weight a rebalance may move in a rolling week.",
    )
    may_pause_sleeves: StrictBool = False
    may_propose_sleeves: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def reject_unknown_authority(cls, data: object) -> object:
        """Refuse any unknown ``may_*`` permission with the manager's fixed boundary."""
        if isinstance(data, dict):
            for key in data:
                if (
                    isinstance(key, str)
                    and key.startswith("may_")
                    and key not in MANAGER_PERMISSION_KEYS
                ):
                    raise ValueError(
                        f"Unknown manager permission {key!r}: the manager may only rebalance "
                        "weights, pause sleeves, and propose sleeves. It never places orders; "
                        "strategies place every trade (ADR 0088)."
                    )
        return data


class ManagerSettings(_FrozenModel):
    """The manager agent's mandate and permissions (the agent loop runs outside ThyTrader)."""

    mandate: MandateText = ""
    permissions: ManagerPermissions = Field(default_factory=ManagerPermissions)


class JournalChange(_FrozenModel):
    """One field's value before and after a change, rendered as display text."""

    field: str = Field(min_length=1, max_length=64)
    before: str | None = Field(default=None, max_length=2000)
    after: str | None = Field(default=None, max_length=2000)


class JournalDetail(_FrozenModel):
    """Structured facts behind one journal entry.

    Deployment events name the sleeve deployments they touched; breaker events carry the
    tripped ``reason_code``; proposal events name the proposal and quote its rationale.
    """

    sleeve_id: UUID | None = None
    strategy_id: UUID | None = None
    strategy_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    reason: JournalReason | None = None
    changes: tuple[JournalChange, ...] = ()
    job_id: UUID | None = None
    result_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    deployment_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_SLEEVES * 2)
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    proposal_id: UUID | None = None
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_LENGTH)
    note: str | None = Field(default=None, max_length=MAX_SUMMARY_LENGTH)


class JournalEntry(_FrozenModel):
    """One append-only portfolio journal event.

    ``revision`` is the portfolio revision the event produced (or, for ``backtest_run``,
    the revision whose composition was simulated).
    """

    entry_id: UUID
    portfolio_id: UUID
    occurred_at: datetime
    kind: JournalKind
    actor: JournalActor
    channel: JournalChannel
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    detail: JournalDetail = Field(default_factory=JournalDetail)
    revision: int = Field(ge=1)

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep journal instants timezone-aware UTC."""
        return require_utc(value)

    @field_serializer("occurred_at", when_used="json")
    def serialize_occurred_at(self, value: datetime) -> str:
        """Render the instant with a Z suffix."""
        return utc_text(value)


class SleeveBatchItem(_FrozenModel):
    """One sleeve for creation or a batch add: a strategy and its capital weight."""

    strategy_id: UUID
    weight_fraction: WeightFractionText
    note: SleeveNoteText | None = None


class PortfolioCreateRequest(_FrozenModel):
    """Create a portfolio with optional sleeves; mode and quote currency are then fixed."""

    name: PortfolioNameText
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency = "USDC"
    capital_quote: QuoteAmountText
    cash_reserve_fraction: ReserveFractionText = "0"
    limits: PortfolioLimits = Field(default_factory=PortfolioLimits)
    manager: ManagerSettings = Field(default_factory=ManagerSettings)
    sleeves: tuple[SleeveBatchItem, ...] = Field(default=(), max_length=MAX_SLEEVES)

    @model_validator(mode="after")
    def require_distinct_strategies(self) -> Self:
        """One sleeve per strategy, including at creation."""
        identities = [item.strategy_id for item in self.sleeves]
        if len(set(identities)) != len(identities):
            raise ValueError("Each strategy may appear once in sleeves.")
        return self


class PortfolioUpdateRequest(_FrozenModel):
    """Change name, capital, cash reserve, limits, or manager settings in one revision."""

    revision: RevisionNumber
    name: PortfolioNameText | None = None
    capital_quote: QuoteAmountText | None = None
    cash_reserve_fraction: ReserveFractionText | None = None
    limits: PortfolioLimits | None = None
    manager: ManagerSettings | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_fixed_fields(cls, data: object) -> object:
        """Explain that mode and quote currency cannot change after creation."""
        if isinstance(data, dict) and ("mode" in data or "quote_currency" in data):
            raise ValueError(
                "mode and quote_currency are fixed when a portfolio is created; "
                "create a new portfolio instead."
            )
        return data

    @model_validator(mode="after")
    def require_change(self) -> Self:
        """Require at least one field to change."""
        if (
            self.name is None
            and self.capital_quote is None
            and self.cash_reserve_fraction is None
            and self.limits is None
            and self.manager is None
        ):
            raise ValueError(
                "Name at least one of name, capital_quote, cash_reserve_fraction, limits, "
                "or manager."
            )
        return self


class SleeveAddRequest(_FrozenModel):
    """Add one strategy as a sleeve with a capital weight."""

    revision: RevisionNumber
    strategy_id: UUID
    weight_fraction: WeightFractionText
    note: SleeveNoteText | None = None


class SleevesAddRequest(_FrozenModel):
    """Add several strategies as sleeves atomically, in one revision (ADR 0094)."""

    revision: RevisionNumber
    sleeves: tuple[SleeveBatchItem, ...] = Field(min_length=1, max_length=MAX_SLEEVES)

    @model_validator(mode="after")
    def require_distinct_strategies(self) -> Self:
        """One sleeve per strategy, within the batch as in the portfolio."""
        identities = [item.strategy_id for item in self.sleeves]
        if len(set(identities)) != len(identities):
            raise ValueError("Each strategy may appear once in sleeves.")
        return self


class SleeveUpdateRequest(_FrozenModel):
    """Change one sleeve's weight and/or note. An empty note clears it."""

    revision: RevisionNumber
    weight_fraction: WeightFractionText | None = None
    note: SleeveNoteText | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        """Require a weight or a note."""
        if self.weight_fraction is None and self.note is None:
            raise ValueError("Name weight_fraction, note, or both.")
        return self


class WeightAssignment(_FrozenModel):
    """One sleeve's new weight."""

    sleeve_id: UUID
    weight_fraction: WeightFractionText


class SetWeightsRequest(_FrozenModel):
    """Replace every sleeve weight (and optionally the cash reserve) atomically."""

    revision: RevisionNumber
    weights: tuple[WeightAssignment, ...] = Field(min_length=1, max_length=MAX_SLEEVES)
    cash_reserve_fraction: ReserveFractionText | None = None


@dataclass(frozen=True, slots=True)
class MutationContext:
    """Who made a change, through which channel, and when (UTC)."""

    actor: JournalActor
    channel: JournalChannel
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class Portfolio:
    """One portfolio row. Decimal fields hold canonical decimal text."""

    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    limits: PortfolioLimits
    manager: ManagerSettings
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Sleeve:
    """One strategy held in a portfolio with a capital weight."""

    sleeve_id: UUID
    portfolio_id: UUID
    strategy_id: UUID
    weight_fraction: str
    note: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SleeveStrategy:
    """Current facts about a sleeve's strategy, read with the sleeve.

    Strategies stay mutable, so these facts can drift after a sleeve is added (an edit can
    invalidate the rules or change the market). Reads report that drift as sleeve issues.
    """

    strategy_id: UUID
    name: str
    product_id: str | None
    covered_product_ids: tuple[str, ...]
    timeframe: str | None
    valid: bool
    current_fingerprint: str | None

    @property
    def quote_currency(self) -> SpotQuoteCurrency | None:
        """Quote currency of the primary product, or None when it is not parseable."""
        if self.product_id is None or not is_spot_product_id(self.product_id):
            return None
        return quote_currency(self.product_id)


@dataclass(frozen=True, slots=True)
class SleeveView:
    """A sleeve plus its strategy's current facts."""

    sleeve: Sleeve
    strategy: SleeveStrategy


@dataclass(frozen=True, slots=True)
class PortfolioAggregate:
    """A portfolio with every sleeve (in creation order)."""

    portfolio: Portfolio
    sleeves: tuple[SleeveView, ...]

    def sleeve(self, sleeve_id: UUID) -> SleeveView:
        """Return one sleeve or raise :class:`PortfolioSleeveNotFoundError`."""
        for view in self.sleeves:
            if view.sleeve.sleeve_id == sleeve_id:
                return view
        raise PortfolioSleeveNotFoundError("Sleeve was not found in this portfolio.")


@dataclass(frozen=True, slots=True)
class PortfolioPage:
    """One page of portfolios (oldest first) plus the total count."""

    portfolios: tuple[PortfolioAggregate, ...]
    total: int


@dataclass(frozen=True, slots=True)
class JournalPage:
    """One newest-first page of journal entries plus the total count."""

    entries: tuple[JournalEntry, ...]
    total: int


@dataclass(frozen=True, slots=True)
class PortfolioDeletion:
    """What deleting one portfolio removed."""

    portfolio_id: UUID
    name: str
    sleeves: int
    journal_entries: int
    backtests: int
    backtest_jobs: int


@dataclass(frozen=True, slots=True)
class PortfolioRuntimeState:
    """Durable runtime state of one deployed portfolio (ADR 0091).

    ``run_started_at`` marks the current run: sleeve deployments created since then make
    up its equity. The breaker latches with a reason until an operator reset, and the UTC
    day-open and high-water-mark equity are the baselines the daily-loss and drawdown
    stops measure against. A portfolio that was never deployed reads as the empty state.
    """

    portfolio_id: UUID
    run_started_at: datetime | None = None
    breaker_reason: BreakerReason | None = None
    breaker_detail: str | None = None
    breaker_latched_at: datetime | None = None
    day_open_equity: Decimal | None = None
    day_open_at: datetime | None = None
    high_water_mark_equity: Decimal | None = None
    last_equity: Decimal | None = None
    last_evaluated_at: datetime | None = None
    revision: int = 0
    """Compare-and-set counter: 0 until the first write, then +1 per write."""

    @property
    def breaker_latched(self) -> bool:
        """True while a tripped portfolio breaker waits for an operator reset."""
        return self.breaker_reason is not None


@dataclass(frozen=True, slots=True)
class PortfolioRuntimeView:
    """A portfolio with its sleeves and its runtime state, read together."""

    aggregate: PortfolioAggregate
    runtime: PortfolioRuntimeState


def require_utc(value: datetime) -> datetime:
    """Require a timezone-aware UTC instant and normalize its timezone object."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


def utc_text(value: datetime) -> str:
    """Render one UTC instant with a Z suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_millisecond(value: datetime) -> datetime:
    """Truncate a UTC instant to the millisecond a UUIDv7 can encode."""
    normalized = value.astimezone(UTC)
    return normalized.replace(microsecond=(normalized.microsecond // 1_000) * 1_000)


def document_product_ids(document: Mapping[str, JsonValue], primary: str | None) -> tuple[str, ...]:
    """Read covered spot product ids from a (possibly invalid) strategy document."""
    products: list[str] = [primary] if primary is not None else []
    raw = document.get("additional_instruments")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            product = item.get("product_id")
            if isinstance(product, str) and is_spot_product_id(product) and product not in products:
                products.append(product)
    return tuple(products)


def sleeve_strategy_from_record(record: StrategyRecord) -> SleeveStrategy:
    """Project one strategy record into the facts a sleeve needs."""
    if record.definition is not None:
        covered = covered_product_ids(record.definition)
    else:
        covered = document_product_ids(record.document, record.product_id)
    return SleeveStrategy(
        strategy_id=record.strategy_id,
        name=record.name,
        product_id=record.product_id,
        covered_product_ids=covered,
        timeframe=record.timeframe,
        valid=record.validation.valid,
        current_fingerprint=record.current_fingerprint,
    )


def sleeve_issues(view: SleeveView, quote: SpotQuoteCurrency) -> tuple[SleeveIssueCode, ...]:
    """Return what keeps one sleeve from being backtested or (later) deployed."""
    issues: list[SleeveIssueCode] = []
    strategy_quote = view.strategy.quote_currency
    if strategy_quote is None:
        issues.append("product_unknown")
    elif strategy_quote != quote:
        issues.append("quote_currency_mismatch")
    if not view.strategy.valid:
        issues.append("strategy_invalid")
    return tuple(issues)


def asset_of(product_id: str) -> str:
    """Return the base asset of one spot product id."""
    return base_currency(product_id)
