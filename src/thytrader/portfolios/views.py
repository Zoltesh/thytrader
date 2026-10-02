"""HTTP response views of portfolios and portfolio backtests (ADR 0088).

Shared by the API routes and the ``thytrader-portfolio`` CLI, which validates API
responses with the same models instead of reading untyped JSON.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, Field

from thytrader.market_data.products import SpotQuoteCurrency  # noqa: TC001 - Pydantic field.
from thytrader.portfolios.backtest import (  # noqa: TC001 - Pydantic field types.
    PortfolioBacktestJob,
    PortfolioBacktestListing,
    PortfolioBacktestResult,
)
from thytrader.portfolios.deployment import (
    PortfolioDeploymentState,  # noqa: TC001 - Pydantic field type.
)
from thytrader.portfolios.models import (
    JournalEntry,
    ManagerSettings,
    PortfolioAggregate,
    PortfolioLimits,
    PortfolioMode,
    SleeveIssueCode,
    SleeveView,
    sleeve_issues,
    utc_text,
)
from thytrader.portfolios.rules import AllocationSummary, allocation_summary, sleeve_capital

DEPLOYABLE_NOTE = (
    "True when the portfolio has at least one sleeve and no sleeve has issues, so a start "
    "can be planned (the risk policy may still refuse a sleeve). Start, pause, resume, and "
    "stop are thytrader-runtime portfolio-* commands (ADR 0091)."
)


class SleeveResponse(BaseModel):
    """One sleeve with its strategy's current facts and what blocks it."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    product_id: str | None
    covered_product_ids: tuple[str, ...]
    timeframe: str | None
    quote_currency: str | None
    strategy_valid: bool
    current_fingerprint: str | None
    weight_fraction: str
    capital_quote: str
    note: str | None
    issues: tuple[SleeveIssueCode, ...]
    created_at: str
    updated_at: str


class AssetAllocationResponse(BaseModel):
    """Weight held in one base asset."""

    asset: str
    weight_fraction: str
    sleeve_ids: tuple[UUID, ...]


class AllocationResponse(BaseModel):
    """Sleeves, cash reserve, unallocated cash, and the largest single asset."""

    allocated_fraction: str
    cash_reserve_fraction: str
    unallocated_fraction: str
    allocated_quote: str
    cash_reserve_quote: str
    unallocated_quote: str
    assets: tuple[AssetAllocationResponse, ...]
    largest_asset: AssetAllocationResponse | None
    largest_asset_within_limit: bool | None = Field(
        description="Largest asset weight vs limits.max_per_asset_fraction; null without sleeves."
    )


class PortfolioResponse(BaseModel):
    """One portfolio with sleeves, allocation, limits, and manager settings."""

    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    revision: int
    created_at: str
    updated_at: str
    limits: PortfolioLimits
    manager: ManagerSettings
    sleeves: tuple[SleeveResponse, ...]
    allocation: AllocationResponse
    deployable: bool = Field(description=DEPLOYABLE_NOTE)
    deployment_state: PortfolioDeploymentState = Field(
        default="not_deployed",
        description="not_deployed, running, partially_running, paused, or stopped.",
    )


class PortfolioListResponse(BaseModel):
    """One page of portfolios, oldest first."""

    portfolios: tuple[PortfolioResponse, ...]
    limit: int
    returned: int
    total: int
    has_more: bool
    next_cursor: str | None = None


class PortfolioDeletionResponse(BaseModel):
    """What deleting a portfolio removed."""

    portfolio_id: UUID
    name: str
    outcome: Literal["deleted"] = "deleted"
    sleeves: int
    journal_entries: int
    backtests: int
    backtest_jobs: int


class JournalListResponse(BaseModel):
    """One newest-first page of the portfolio journal."""

    entries: tuple[JournalEntry, ...]
    limit: int
    returned: int
    total: int
    has_more: bool
    next_cursor: str | None = None


class PlannedSleeveResponse(BaseModel):
    """What one sleeve will run: snapshot, capital slice, and decision dataset."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_fingerprint: str
    capital_quote: str
    dataset_fingerprint: str


class PortfolioBacktestAcceptedResponse(BaseModel):
    """HTTP 202 body: the queued job and the plan it will run."""

    job: PortfolioBacktestJob
    sleeves: tuple[PlannedSleeveResponse, ...]


class PortfolioBacktestJobListResponse(BaseModel):
    """The portfolio's newest backtest jobs first."""

    jobs: tuple[PortfolioBacktestJob, ...]
    limit: int
    returned: int


class PortfolioBacktestListResponse(BaseModel):
    """One newest-first page of stored portfolio backtests (no curves)."""

    entries: tuple[PortfolioBacktestListing, ...]
    limit: int
    offset: int
    returned: int
    has_more: bool
    next_cursor: str | None = None


class PortfolioBacktestDetailResponse(BaseModel):
    """One stored result; the curve may be thinned for display (``max_points``)."""

    result_fingerprint: str
    result: PortfolioBacktestResult
    equity_curve_points: int = Field(description="Points in the canonical (unthinned) curve.")
    equity_curve_downsampled: bool


def portfolio_response(
    aggregate: PortfolioAggregate,
    *,
    deployment_state: PortfolioDeploymentState = "not_deployed",
) -> PortfolioResponse:
    """Project one aggregate (and its deployment state) into its HTTP body."""
    portfolio = aggregate.portfolio
    deployable = bool(aggregate.sleeves) and not any(
        sleeve_issues(view, portfolio.quote_currency) for view in aggregate.sleeves
    )
    return PortfolioResponse(
        portfolio_id=portfolio.portfolio_id,
        name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        capital_quote=portfolio.capital_quote,
        cash_reserve_fraction=portfolio.cash_reserve_fraction,
        revision=portfolio.revision,
        created_at=utc_text(portfolio.created_at),
        updated_at=utc_text(portfolio.updated_at),
        limits=portfolio.limits,
        manager=portfolio.manager,
        sleeves=tuple(_sleeve_response(aggregate, view) for view in aggregate.sleeves),
        allocation=_allocation_response(allocation_summary(aggregate)),
        deployable=deployable,
        deployment_state=deployment_state,
    )


def _sleeve_response(aggregate: PortfolioAggregate, view: SleeveView) -> SleeveResponse:
    """Project one sleeve and its issues."""
    sleeve = view.sleeve
    strategy = view.strategy
    return SleeveResponse(
        sleeve_id=sleeve.sleeve_id,
        strategy_id=sleeve.strategy_id,
        strategy_name=strategy.name,
        product_id=strategy.product_id,
        covered_product_ids=strategy.covered_product_ids,
        timeframe=strategy.timeframe,
        quote_currency=strategy.quote_currency,
        strategy_valid=strategy.valid,
        current_fingerprint=strategy.current_fingerprint,
        weight_fraction=sleeve.weight_fraction,
        capital_quote=sleeve_capital(aggregate.portfolio.capital_quote, sleeve.weight_fraction),
        note=sleeve.note,
        issues=sleeve_issues(view, aggregate.portfolio.quote_currency),
        created_at=utc_text(sleeve.created_at),
        updated_at=utc_text(sleeve.updated_at),
    )


def _allocation_response(summary: AllocationSummary) -> AllocationResponse:
    """Project the allocation summary."""
    assets = tuple(
        AssetAllocationResponse(
            asset=item.asset, weight_fraction=item.weight_fraction, sleeve_ids=item.sleeve_ids
        )
        for item in summary.assets
    )
    return AllocationResponse(
        allocated_fraction=summary.allocated_fraction,
        cash_reserve_fraction=summary.cash_reserve_fraction,
        unallocated_fraction=summary.unallocated_fraction,
        allocated_quote=summary.allocated_quote,
        cash_reserve_quote=summary.cash_reserve_quote,
        unallocated_quote=summary.unallocated_quote,
        assets=assets,
        largest_asset=assets[0] if assets else None,
        largest_asset_within_limit=summary.largest_asset_within_limit,
    )
