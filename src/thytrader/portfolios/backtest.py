"""Portfolio backtest contracts: request, resolved plan, combined result, and job records.

A portfolio backtest runs every sleeve through the unified backtest model (ADR 0083) on
its own fixed capital slice (``weight * capital_quote``) over one common evaluation
window, then combines the sleeve equity curves (ADR 0088). The canonical result is
content-addressed like a backtest result and references every child result fingerprint.
Sleeves are simulated independently: portfolio caps and cross-sleeve interactions are
not simulated, and the result says so in ``disclosures``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Final, Literal, Self
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.backtest.models import ResultDecimalText  # noqa: TC001 - Pydantic field type.
from thytrader.backtest.submission import BacktestSubmissionRequest  # noqa: TC001
from thytrader.market_data.models import DatasetTimeframe  # noqa: TC001 - Pydantic field type.
from thytrader.market_data.products import SpotQuoteCurrency  # noqa: TC001 - Pydantic field.
from thytrader.portfolios.models import (
    MAX_SLEEVES,
    PORTFOLIO_BACKTEST_CONTRACT,
    PortfolioMode,
    RevisionNumber,
    require_utc,
    utc_text,
)
from thytrader.research.jobs import ResearchJobStatus
from thytrader.research.models import (
    BACKTEST_ENGINE,
    AdditionalInstrumentDataset,
    BacktestEngine,
    CostAssumptions,
    DecimalText,
    FingerprintText,
    IndicatorTimeframeDataset,
    reject_removed_engine_selection,
)

PortfolioBacktestContract = Literal["thytrader-portfolio-backtest-v1"]
PORTFOLIO_BACKTEST_EXPIRY_HOURS: Final = 24
PORTFOLIO_BACKTEST_STATUSES: Final[tuple[str, ...]] = tuple(
    item.value for item in ResearchJobStatus
)

INDEPENDENT_SLEEVES_DISCLOSURE: Final = (
    "Sleeves simulated independently on fixed capital slices; portfolio-level caps and "
    "cross-sleeve interactions are not simulated."
)
MODEL_DISCLOSURE: Final = (
    "Each sleeve runs the unified backtest model (engine thytrader-backtest) with the stated "
    "maker/taker fees, fixed slippage, and spread stress. Fills are simulated from candles, "
    "not historical executions."
)
CASH_DISCLOSURE: Final = (
    "The cash reserve and any unallocated capital are held as cash and earn nothing."
)
GRID_DISCLOSURE: Final = (
    "Combined equity sums each sleeve's latest mark, forward-filled on the union of every "
    "sleeve's bar closes, plus cash. Ratio metrics annualize on that grid's median spacing."
)
CORRELATION_DISCLOSURE: Final = (
    "Correlations use sleeve returns sampled on the coarsest common bar clock of all sleeves."
)
BASKET_DISCLOSURE: Final = (
    "The equal-weight basket buys each sleeve's primary product with an equal share of the "
    "invested capital at the first evaluation open (taker fee, fixed slippage, half the "
    "spread stress), marks at bar closes, and sells at the open of the window-end bar. The "
    "cash reserve stays cash."
)
MULTI_PRODUCT_DISCLOSURE: Final = (
    "Overlap and long-together time exclude multi-product sleeves: their results do not "
    "attribute positions to one product."
)


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SleeveDatasetOverride(_FrozenModel):
    """Exact dataset fingerprints for one sleeve's strategy, replacing the latest datasets.

    Supply every clock the strategy declares: the decision dataset, the HTF dataset when it
    has an HTF filter, extra indicator clocks, and additional instruments, exactly as a
    single backtest request would.
    """

    strategy_id: UUID
    dataset_fingerprint: FingerprintText
    htf_dataset_fingerprint: FingerprintText | None = None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = ()
    additional_instrument_datasets: tuple[AdditionalInstrumentDataset, ...] = ()


class PortfolioBacktestRequest(_FrozenModel):
    """Run every sleeve over one common window with the same cost assumptions.

    Omit ``evaluation_start``/``evaluation_end`` to use the intersection of every sleeve's
    usable coverage, aligned to the coarsest sleeve clock. ``revision``, when given, must be
    the portfolio's current revision (a stale one is a 409 conflict).
    """

    revision: RevisionNumber | None = None
    maker_fee_rate: DecimalText
    taker_fee_rate: DecimalText
    fixed_slippage_bps: DecimalText
    spread_bps: DecimalText | None = None
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    datasets: tuple[SleeveDatasetOverride, ...] = Field(default=(), max_length=MAX_SLEEVES)

    @model_validator(mode="before")
    @classmethod
    def reject_engine_selection(cls, data: object) -> object:
        """Reject the retired engine selector with the ADR 0083 migration message."""
        return reject_removed_engine_selection(data)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        """Require both-or-neither dates, valid costs, and one override per strategy."""
        if (self.evaluation_start is None) != (self.evaluation_end is None):
            raise ValueError("evaluation_start and evaluation_end must both be omitted or set.")
        if self.evaluation_start is not None:
            require_utc(self.evaluation_start)
        if self.evaluation_end is not None:
            require_utc(self.evaluation_end)
        self.costs()
        identities = [item.strategy_id for item in self.datasets]
        if len(identities) != len(set(identities)):
            raise ValueError("datasets may name each strategy_id at most once.")
        return self

    def costs(self) -> CostAssumptions:
        """Return the validated cost assumptions every sleeve shares."""
        return CostAssumptions(
            maker_fee_rate=self.maker_fee_rate,
            taker_fee_rate=self.taker_fee_rate,
            fixed_slippage_bps=self.fixed_slippage_bps,
            spread_bps=self.spread_bps if self.spread_bps is not None else "0",
        )


class PlannedSleeve(_FrozenModel):
    """One sleeve's snapshot, capital slice, and complete child backtest submission."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    strategy_fingerprint: FingerprintText
    product_id: str
    covered_product_ids: tuple[str, ...]
    timeframe: DatasetTimeframe
    weight_fraction: str
    capital_quote: str
    submission: BacktestSubmissionRequest


class PortfolioBacktestPlan(_FrozenModel):
    """Everything a portfolio backtest job needs, resolved when it was accepted."""

    contract: PortfolioBacktestContract = PORTFOLIO_BACKTEST_CONTRACT
    portfolio_id: UUID
    portfolio_revision: int = Field(ge=1)
    portfolio_name: str
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    evaluation_start: datetime
    evaluation_end: datetime
    costs: CostAssumptions
    sleeves: tuple[PlannedSleeve, ...] = Field(min_length=1, max_length=MAX_SLEEVES)

    @field_validator("evaluation_start", "evaluation_end")
    @classmethod
    def require_utc_bounds(cls, value: datetime) -> datetime:
        """Keep window bounds timezone-aware UTC."""
        return require_utc(value)


class PortfolioSleeveResult(_FrozenModel):
    """One sleeve's standalone result and its share of the portfolio outcome.

    ``contribution_fraction`` is the sleeve's PnL divided by portfolio capital, so the
    contributions sum to the portfolio's total return (cash contributes zero).
    """

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    strategy_fingerprint: FingerprintText
    product_id: str
    covered_product_ids: tuple[str, ...]
    timeframe: DatasetTimeframe
    weight_fraction: str
    capital_quote: ResultDecimalText
    run_fingerprint: FingerprintText
    result_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    final_equity: ResultDecimalText
    total_return_fraction: ResultDecimalText
    maximum_drawdown_fraction: ResultDecimalText
    trade_count: int = Field(ge=0)
    win_rate: ResultDecimalText
    contribution_fraction: ResultDecimalText
    correlation_to_rest: ResultDecimalText | None = None
    long_fraction: ResultDecimalText | None = None


class PortfolioBacktestSummary(_FrozenModel):
    """Headline numbers of the combined equity curve."""

    initial_equity: ResultDecimalText
    final_equity: ResultDecimalText
    total_net_pnl: ResultDecimalText
    total_return_fraction: ResultDecimalText
    maximum_drawdown: ResultDecimalText
    maximum_drawdown_fraction: ResultDecimalText
    idle_capital_fraction: ResultDecimalText
    allocated_fraction: ResultDecimalText
    cash_quote: ResultDecimalText
    trade_count: int = Field(ge=0)
    best_sleeve_return_fraction: ResultDecimalText
    best_sleeve_maximum_drawdown_fraction: ResultDecimalText
    grid_points: int = Field(ge=2)


class PortfolioCurveMetrics(_FrozenModel):
    """Ratio metrics of the combined curve (performance-metrics formulas, rf = 0)."""

    risk_free_rate: Literal["0"] = "0"
    annualization: Literal["union_grid_median_spacing"] = "union_grid_median_spacing"
    bar_seconds: ResultDecimalText | None = None
    bars_per_year: ResultDecimalText | None = None
    sharpe: ResultDecimalText | None = None
    sortino: ResultDecimalText | None = None
    calmar: ResultDecimalText | None = None
    cagr: ResultDecimalText | None = None
    annualized_volatility: ResultDecimalText | None = None


class CorrelationPair(_FrozenModel):
    """Pearson correlation of two sleeves' returns; null when it is undefined."""

    sleeve_ids: tuple[UUID, UUID]
    coefficient: ResultDecimalText | None = None


class PortfolioCorrelation(_FrozenModel):
    """Pairwise sleeve-return correlations on the coarsest common bar clock."""

    return_clock_seconds: int = Field(ge=60)
    observations: int = Field(ge=0)
    pairs: tuple[CorrelationPair, ...] = ()


class OverlapPair(_FrozenModel):
    """Fraction of the window two sleeves were both long the same asset."""

    sleeve_ids: tuple[UUID, UUID]
    asset: str
    fraction: ResultDecimalText


class PortfolioOverlap(_FrozenModel):
    """How often sleeves held long positions at the same time (time-weighted).

    ``same_asset_fraction`` is the share of the window with at least two sleeves long the
    same asset; ``long_together_fraction`` counts any two sleeves long at once.
    """

    same_asset_fraction: ResultDecimalText
    long_together_fraction: ResultDecimalText
    pairs: tuple[OverlapPair, ...] = ()
    excluded_sleeve_ids: tuple[UUID, ...] = ()


class BasketLeg(_FrozenModel):
    """One product of the equal-weight buy-and-hold basket."""

    product_id: str
    dataset_fingerprint: FingerprintText
    timeframe: DatasetTimeframe
    entry_price: ResultDecimalText
    exit_price: ResultDecimalText
    quantity: ResultDecimalText
    return_fraction: ResultDecimalText


class PortfolioBasketBenchmark(_FrozenModel):
    """Buy-and-hold of an equal-weight basket of the sleeves' primary products."""

    legs: tuple[BasketLeg, ...] = Field(min_length=1)
    invested_quote: ResultDecimalText
    cash_quote: ResultDecimalText
    final_equity: ResultDecimalText
    total_return_fraction: ResultDecimalText
    maximum_drawdown_fraction: ResultDecimalText
    total_fees: ResultDecimalText


class PortfolioEquityPoint(_FrozenModel):
    """Combined and basket equity at one grid instant (a bar close of some sleeve)."""

    at: datetime
    equity: ResultDecimalText
    basket_equity: ResultDecimalText

    @field_validator("at")
    @classmethod
    def require_utc_at(cls, value: datetime) -> datetime:
        """Keep grid instants timezone-aware UTC."""
        return require_utc(value)

    @field_serializer("at", when_used="json")
    def serialize_at(self, value: datetime) -> str:
        """Render grid instants with a Z suffix."""
        return utc_text(value)


class PortfolioBacktestResult(_FrozenModel):
    """Canonical combined result of one portfolio backtest."""

    schema_version: Literal["1.0"] = "1.0"
    contract: PortfolioBacktestContract = PORTFOLIO_BACKTEST_CONTRACT
    engine: BacktestEngine = BACKTEST_ENGINE
    portfolio_id: UUID
    portfolio_revision: int = Field(ge=1)
    portfolio_name: str
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    evaluation_start: datetime
    evaluation_end: datetime
    costs: CostAssumptions
    sleeves: tuple[PortfolioSleeveResult, ...] = Field(min_length=1)
    summary: PortfolioBacktestSummary
    metrics: PortfolioCurveMetrics
    correlation: PortfolioCorrelation
    overlap: PortfolioOverlap
    basket: PortfolioBasketBenchmark
    equity_curve: tuple[PortfolioEquityPoint, ...] = Field(min_length=2)
    disclosures: tuple[str, ...] = Field(min_length=1)

    @field_validator("evaluation_start", "evaluation_end")
    @classmethod
    def require_utc_bounds(cls, value: datetime) -> datetime:
        """Keep window bounds timezone-aware UTC."""
        return require_utc(value)

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_bounds(self, value: datetime) -> str:
        """Render window bounds with a Z suffix."""
        return utc_text(value)


class PortfolioBacktestListing(_FrozenModel):
    """One stored portfolio backtest without its equity curve."""

    result_fingerprint: FingerprintText
    portfolio_id: UUID
    portfolio_revision: int
    published_at: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    sleeve_count: int = Field(ge=1)
    total_return_fraction: ResultDecimalText
    maximum_drawdown_fraction: ResultDecimalText
    idle_capital_fraction: ResultDecimalText
    basket_total_return_fraction: ResultDecimalText
    trade_count: int = Field(ge=0)

    @field_serializer("published_at", "evaluation_start", "evaluation_end", when_used="json")
    def serialize_instants(self, value: datetime) -> str:
        """Render instants with a Z suffix."""
        return utc_text(value)


class PortfolioBacktestJob(_FrozenModel):
    """One async portfolio backtest tracked until it completes, fails, or expires.

    Progress counts finished sleeves plus the final combination step.
    """

    job_id: UUID
    portfolio_id: UUID
    portfolio_revision: int = Field(ge=1)
    status: ResearchJobStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    sleeve_count: int = Field(ge=1)
    progress_current: int = Field(default=0, ge=0)
    progress_total: int = Field(default=1, ge=0)
    error_message: str | None = None
    failed_detail: str | None = None
    result_fingerprint: FingerprintText | None = None

    @field_serializer(
        "created_at",
        "updated_at",
        "expires_at",
        "evaluation_start",
        "evaluation_end",
        when_used="json",
    )
    def serialize_instants(self, value: datetime) -> str:
        """Render instants with a Z suffix."""
        return utc_text(value)


def downsample_curve(
    points: tuple[PortfolioEquityPoint, ...], max_points: int
) -> tuple[PortfolioEquityPoint, ...]:
    """Thin a curve for display, keeping the first, last, and each bucket's low and high.

    Display only: canonical results and fingerprints always keep every grid point. Each of
    ``(max_points - 2) // 2`` buckets keeps its lowest and highest equity point in time
    order, so drawdowns stay visible.
    """
    if max_points < 4 or len(points) <= max_points:
        return points
    inner = points[1:-1]
    buckets = max(1, (max_points - 2) // 2)
    size = -(-len(inner) // buckets)
    kept: list[PortfolioEquityPoint] = [points[0]]
    for start in range(0, len(inner), size):
        bucket = inner[start : start + size]
        low = min(bucket, key=lambda point: Decimal(point.equity))
        high = max(bucket, key=lambda point: Decimal(point.equity))
        kept.extend(sorted({low.at: low, high.at: high}.values(), key=lambda point: point.at))
    kept.append(points[-1])
    return tuple(kept)


def canonical_portfolio_backtest_bytes(result: PortfolioBacktestResult) -> bytes:
    """Revalidate and encode one result as sorted compact canonical UTF-8 JSON."""
    validated = PortfolioBacktestResult.model_validate(result.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def portfolio_backtest_fingerprint(result: PortfolioBacktestResult) -> str:
    """Return the SHA-256 content identity of one canonical portfolio backtest."""
    return f"sha256:{sha256(canonical_portfolio_backtest_bytes(result)).hexdigest()}"


def portfolio_backtest_listing(
    result: PortfolioBacktestResult, *, result_fingerprint: str, published_at: datetime
) -> PortfolioBacktestListing:
    """Project one result into its list row."""
    return PortfolioBacktestListing(
        result_fingerprint=result_fingerprint,
        portfolio_id=result.portfolio_id,
        portfolio_revision=result.portfolio_revision,
        published_at=published_at,
        evaluation_start=result.evaluation_start,
        evaluation_end=result.evaluation_end,
        sleeve_count=len(result.sleeves),
        total_return_fraction=result.summary.total_return_fraction,
        maximum_drawdown_fraction=result.summary.maximum_drawdown_fraction,
        idle_capital_fraction=result.summary.idle_capital_fraction,
        basket_total_return_fraction=result.basket.total_return_fraction,
        trade_count=result.summary.trade_count,
    )


def backtest_journal_summary(result: PortfolioBacktestResult) -> str:
    """One journal sentence for a completed portfolio backtest."""
    total = _signed_percent(Decimal(result.summary.total_return_fraction))
    drawdown = _signed_percent(-Decimal(result.summary.maximum_drawdown_fraction))
    count = len(result.sleeves)
    return (
        f"Portfolio backtest: {total} net, {drawdown} max drawdown, "
        f"{result.evaluation_start.date().isoformat()} → "
        f"{result.evaluation_end.date().isoformat()} ({count} sleeve{'s' if count != 1 else ''})."
    )


def _signed_percent(fraction: Decimal) -> str:
    """Render a fraction as a signed percent rounded half-even to one decimal place."""
    value = (fraction * 100).quantize(Decimal("0.1"))
    if value == 0:
        return "0.0%"
    sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value)}%"


def job_expiry(created_at: datetime) -> datetime:
    """Return when a queued or running job expires."""
    return created_at + timedelta(hours=PORTFOLIO_BACKTEST_EXPIRY_HOURS)
