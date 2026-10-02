"""HTTP views of a deployed portfolio and its proposals (ADR 0091).

Shared by the API routes, the ``thytrader-portfolio`` and ``thytrader-runtime`` CLIs
(which validate responses with these models), and the manager briefing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, Field

from thytrader.execution.models import DeploymentMode
from thytrader.portfolios.deployment import (
    PortfolioDeploymentState,
    daily_pnl,
    drawdown_fraction,
    net_pnl,
    roll_baselines,
)
from thytrader.portfolios.models import (
    PortfolioMode,
    SleeveIssueCode,
    sleeve_issues,
    utc_text,
)
from thytrader.portfolios.proposals import Proposal  # noqa: TC001 - Pydantic field type.
from thytrader.research.indicators import canonical_decimal
from thytrader.risk.exposure import risk_bearing_snapshots
from thytrader.risk.gate import portfolio_exposure

if TYPE_CHECKING:
    from thytrader.execution.models import Deployment, DeploymentSnapshot
    from thytrader.market_data.products import SpotQuoteCurrency
    from thytrader.portfolios.deployment import SleeveBook
    from thytrader.portfolios.runtime import PortfolioActionResult, PortfolioDeploymentSnapshot

_ZERO = Decimal(0)
_FRACTION = Decimal("0.000001")


class SleeveDeploymentResponse(BaseModel):
    """One sleeve's bot as the portfolio sees it (open it at ``/deployments/{id}``)."""

    deployment_id: UUID
    strategy_id: UUID | None
    strategy_name: str | None
    status: str
    phase: str
    lifecycle_command: str
    mismatch_detail: str | None
    allocated_capital: str | None
    paper_starting_cash: str | None
    performance_equity: str | None
    net_pnl: str = Field(description="Persisted bar-close equity minus starting equity.")
    return_fraction: str | None = Field(
        description="net_pnl over the sleeve's capital (starting cash or allocation)."
    )
    drawdown_fraction: str | None = Field(
        description="Drawdown of the sleeve's equity from its own peak this run."
    )
    exposure_quote: str
    open_books: int
    strategy_fingerprint: str | None
    running_current_rules: bool | None = Field(
        description="False when the bot runs an earlier edit of its strategy."
    )
    created_at: str
    updated_at: str


class SleeveBookResponse(BaseModel):
    """One sleeve with its target capital and its current bot (null before a start)."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    product_id: str | None
    timeframe: str | None
    weight_fraction: str
    target_capital_quote: str
    issues: tuple[SleeveIssueCode, ...]
    deployment: SleeveDeploymentResponse | None


class PortfolioBreakerResponse(BaseModel):
    """The portfolio breakers: their limits, the latch, and the baselines they watch."""

    latched: bool
    reason_code: str | None
    detail: str | None
    latched_at: str | None
    daily_loss_quote: str | None
    max_drawdown_fraction: str | None
    run_started_at: str | None
    equity: str = Field(description="Capital plus every run book's net PnL, now.")
    day_open_equity: str | None
    daily_pnl: str | None
    high_water_mark_equity: str | None
    drawdown_fraction: str | None
    evaluated_at: str | None = Field(description="When the worker last recorded equity.")


class AssetExposureResponse(BaseModel):
    """Exposure in one base asset against the per-asset cap."""

    asset: str
    exposure_quote: str
    fraction_of_capital: str
    cap_quote: str


class PortfolioExposureResponse(BaseModel):
    """Exposure as the entry gate counts it, against the portfolio's caps."""

    total_quote: str
    fraction_of_capital: str
    cap_quote: str
    asset_cap_quote: str
    assets: tuple[AssetExposureResponse, ...]


class PortfolioDeploymentResponse(BaseModel):
    """A portfolio's deployment: state, each sleeve's bot, breakers, and exposure."""

    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    quote_currency: str
    capital_quote: str
    revision: int
    state: PortfolioDeploymentState
    sleeves: tuple[SleeveBookResponse, ...]
    detached: tuple[SleeveDeploymentResponse, ...] = Field(
        description="Running or paused bots of sleeves that were removed afterwards."
    )
    breaker: PortfolioBreakerResponse
    exposure: PortfolioExposureResponse
    pending_proposals: int


class SleeveOutcomeResponse(BaseModel):
    """What one action did to one sleeve."""

    sleeve_id: UUID | None
    strategy_id: UUID | None
    strategy_name: str
    outcome: Literal["started", "attached", "paused", "resumed", "stopped", "unchanged", "failed"]
    deployment_id: UUID | None
    message: str | None


class PortfolioActionResponse(BaseModel):
    """One start, pause, resume, or stop with per-sleeve outcomes and the new state."""

    action: Literal["start", "pause", "resume", "stop"]
    outcomes: tuple[SleeveOutcomeResponse, ...]
    deployment: PortfolioDeploymentResponse


class ProposalListResponse(BaseModel):
    """One newest-first page of proposals."""

    proposals: tuple[Proposal, ...]
    limit: int
    returned: int
    total: int
    has_more: bool
    next_cursor: str | None = None


class ProposalResponse(BaseModel):
    """One proposal after a submit or decision, with the portfolio revision it left."""

    proposal: Proposal
    portfolio_revision: int


def deployment_response(
    snapshot: PortfolioDeploymentSnapshot, *, pending_proposals: int
) -> PortfolioDeploymentResponse:
    """Project a portfolio's deployment snapshot."""
    aggregate = snapshot.aggregate
    portfolio = aggregate.portfolio
    by_id = {item.deployment.id: item for item in snapshot.snapshots}
    return PortfolioDeploymentResponse(
        portfolio_id=portfolio.portfolio_id,
        name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        capital_quote=portfolio.capital_quote,
        revision=portfolio.revision,
        state=snapshot.books.state,
        sleeves=tuple(
            _sleeve_book(book, by_id, quote=portfolio.quote_currency)
            for book in snapshot.books.sleeves
        ),
        detached=tuple(
            sleeve_deployment(item, by_id.get(item.id), current_fingerprint=None)
            for item in snapshot.books.detached
        ),
        breaker=_breaker(snapshot),
        exposure=_exposure(snapshot),
        pending_proposals=pending_proposals,
    )


def action_response(
    result: PortfolioActionResult,
    snapshot: PortfolioDeploymentSnapshot,
    *,
    pending_proposals: int,
) -> PortfolioActionResponse:
    """Project one action's outcomes plus the deployment state after it."""
    return PortfolioActionResponse(
        action=result.action,
        outcomes=tuple(
            SleeveOutcomeResponse(
                sleeve_id=item.sleeve_id,
                strategy_id=item.strategy_id,
                strategy_name=item.strategy_name,
                outcome=item.outcome,
                deployment_id=item.deployment_id,
                message=item.message,
            )
            for item in result.outcomes
        ),
        deployment=deployment_response(snapshot, pending_proposals=pending_proposals),
    )


def _sleeve_book(
    book: SleeveBook, by_id: dict[UUID, DeploymentSnapshot], *, quote: SpotQuoteCurrency
) -> SleeveBookResponse:
    """Project one sleeve and its bot."""
    view = book.view
    deployment = book.deployment
    return SleeveBookResponse(
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=view.strategy.name,
        product_id=view.strategy.product_id,
        timeframe=view.strategy.timeframe,
        weight_fraction=view.sleeve.weight_fraction,
        target_capital_quote=canonical_decimal(book.target_capital),
        issues=sleeve_issues(view, quote),
        deployment=(
            None
            if deployment is None
            else sleeve_deployment(
                deployment,
                by_id.get(deployment.id),
                current_fingerprint=view.strategy.current_fingerprint,
            )
        ),
    )


def sleeve_capital_base(deployment: Deployment) -> Decimal | None:
    """The capital a sleeve's return is measured on: paper starting cash, else allocation."""
    if deployment.mode is DeploymentMode.PAPER and deployment.paper_starting_cash is not None:
        return deployment.paper_starting_cash
    return deployment.allocated_capital


def sleeve_drawdown(deployment: Deployment) -> Decimal | None:
    """Drawdown of a sleeve's capital-plus-PnL from its own peak this run."""
    base = sleeve_capital_base(deployment)
    if base is None or base <= 0:
        return None
    pnl = net_pnl(deployment)
    peak_pnl = pnl
    if deployment.high_water_mark_equity is not None:
        starting = deployment.initial_equity or deployment.paper_starting_cash or _ZERO
        peak_pnl = max(pnl, deployment.high_water_mark_equity - starting)
    peak = base + max(peak_pnl, _ZERO)
    equity = base + pnl
    if peak <= 0:
        return None
    return max(_ZERO, (peak - equity) / peak)


def sleeve_deployment(
    deployment: Deployment,
    snapshot: DeploymentSnapshot | None,
    *,
    current_fingerprint: str | None,
) -> SleeveDeploymentResponse:
    """Project one sleeve bot."""
    pnl = net_pnl(deployment)
    base = sleeve_capital_base(deployment)
    exposure = (
        _ZERO
        if snapshot is None or deployment.portfolio_id is None
        else portfolio_exposure(deployment.portfolio_id, (snapshot,)).total
    )
    drawdown = sleeve_drawdown(deployment)
    return SleeveDeploymentResponse(
        deployment_id=deployment.id,
        strategy_id=deployment.strategy_id,
        strategy_name=deployment.strategy_name,
        status=deployment.status.value,
        phase=deployment.phase.value,
        lifecycle_command=deployment.lifecycle_command.value,
        mismatch_detail=deployment.mismatch_detail,
        allocated_capital=_optional(deployment.allocated_capital),
        paper_starting_cash=_optional(deployment.paper_starting_cash),
        performance_equity=_optional(deployment.performance_equity),
        net_pnl=canonical_decimal(pnl),
        return_fraction=None if base is None or base <= 0 else _fraction(pnl / base),
        drawdown_fraction=None if drawdown is None else _fraction(drawdown),
        exposure_quote=canonical_decimal(exposure),
        open_books=0 if snapshot is None else len(snapshot.positions),
        strategy_fingerprint=deployment.strategy_fingerprint,
        running_current_rules=(
            None
            if current_fingerprint is None
            else deployment.strategy_fingerprint == current_fingerprint
        ),
        created_at=utc_text(deployment.created_at),
        updated_at=utc_text(deployment.updated_at),
    )


def _breaker(snapshot: PortfolioDeploymentSnapshot) -> PortfolioBreakerResponse:
    """The breakers with today's equity overlaid on the recorded baselines."""
    runtime = snapshot.runtime
    limits = snapshot.aggregate.portfolio.limits
    live = runtime
    if runtime.run_started_at is not None:
        evaluated_at = runtime.last_evaluated_at or runtime.run_started_at
        live = roll_baselines(runtime, equity=snapshot.equity, now=_latest(evaluated_at))
    change = daily_pnl(live) if runtime.run_started_at is not None else None
    drawdown = drawdown_fraction(live) if runtime.run_started_at is not None else None
    return PortfolioBreakerResponse(
        latched=runtime.breaker_latched,
        reason_code=runtime.breaker_reason,
        detail=runtime.breaker_detail,
        latched_at=_optional_time(runtime.breaker_latched_at),
        daily_loss_quote=limits.daily_loss_quote,
        max_drawdown_fraction=limits.max_drawdown_fraction,
        run_started_at=_optional_time(runtime.run_started_at),
        equity=canonical_decimal(snapshot.equity),
        day_open_equity=_optional(live.day_open_equity),
        daily_pnl=None if change is None else canonical_decimal(change),
        high_water_mark_equity=_optional(live.high_water_mark_equity),
        drawdown_fraction=None if drawdown is None else _fraction(drawdown),
        evaluated_at=_optional_time(runtime.last_evaluated_at),
    )


def _latest(moment: datetime) -> datetime:
    """Now, or the recorded instant when the clock reads earlier (never roll backwards)."""
    now = datetime.now(UTC)
    return now if now >= moment else moment


def _exposure(snapshot: PortfolioDeploymentSnapshot) -> PortfolioExposureResponse:
    """Exposure against the total and per-asset caps."""
    portfolio = snapshot.aggregate.portfolio
    capital = Decimal(portfolio.capital_quote)
    mode = DeploymentMode.LIVE if portfolio.mode == "live" else DeploymentMode.PAPER
    exposure = portfolio_exposure(
        portfolio.portfolio_id, risk_bearing_snapshots(snapshot.snapshots, mode)
    )
    asset_cap = capital * Decimal(portfolio.limits.max_per_asset_fraction)
    return PortfolioExposureResponse(
        total_quote=canonical_decimal(exposure.total),
        fraction_of_capital=_fraction(exposure.total / capital) if capital > 0 else "0",
        cap_quote=canonical_decimal(
            capital * Decimal(portfolio.limits.max_total_exposure_fraction)
        ),
        asset_cap_quote=canonical_decimal(asset_cap),
        assets=tuple(
            AssetExposureResponse(
                asset=asset,
                exposure_quote=canonical_decimal(amount),
                fraction_of_capital=_fraction(amount / capital) if capital > 0 else "0",
                cap_quote=canonical_decimal(asset_cap),
            )
            for asset, amount in sorted(
                exposure.assets.items(), key=lambda item: (-item[1], item[0])
            )
        ),
    )


def _fraction(value: Decimal) -> str:
    """A fraction rounded to six places, canonical."""
    return canonical_decimal(value.quantize(_FRACTION))


def _optional(value: Decimal | None) -> str | None:
    """Canonical text for an optional decimal."""
    return None if value is None else canonical_decimal(value)


def _optional_time(value: datetime | None) -> str | None:
    """UTC text for an optional instant."""
    return None if value is None else utc_text(value)
