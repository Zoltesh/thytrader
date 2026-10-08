"""Pure portfolio deployment rules: sleeve books, run equity, and breakers (ADR 0091).

A deployed portfolio runs one deployment (bot) per sleeve, each tagged with the
portfolio's id. Strategies place every trade inside their own books; the portfolio adds
shared limits on top. This module reads those books without I/O:

* each sleeve's current book (the running or paused one, else its newest);
* the portfolio's state (not deployed, running, partially running, paused, stopped);
* the current run's equity: portfolio capital plus every run book's net PnL, where a
  book's net PnL is its persisted (bar-close marked) equity minus its starting equity;
* the breakers: the UTC-day loss stop (``daily_loss_quote``) and the drawdown stop from
  the run's high-water mark (``max_drawdown_fraction``). A trip latches until reset.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal

from thytrader.decimal_text import canonical_decimal
from thytrader.portfolios.allocation import percent_text, quote_text, sleeve_capital
from thytrader.risk.models import RiskReasonCode
from thytrader.risk.portfolio_limits import PortfolioRiskBook
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import Deployment, DeploymentMode, DeploymentStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from thytrader.portfolios.models import PortfolioAggregate, PortfolioRuntimeState, SleeveView
    from thytrader.portfolios.vocabulary import BreakerReason

PortfolioDeploymentState = Literal[
    "not_deployed", "running", "partially_running", "paused", "stopped"
]
PORTFOLIO_DEPLOYMENT_STATES: Final[tuple[PortfolioDeploymentState, ...]] = (
    "not_deployed",
    "running",
    "partially_running",
    "paused",
    "stopped",
)
_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class SleeveBook:
    """One sleeve, its target capital, and its current deployment (if any)."""

    view: SleeveView
    target_capital: Decimal
    deployment: Deployment | None


@dataclass(frozen=True, slots=True)
class PortfolioBooks:
    """Every sleeve's book plus tagged books whose sleeve is gone (removed sleeves)."""

    sleeves: tuple[SleeveBook, ...]
    detached: tuple[Deployment, ...]
    state: PortfolioDeploymentState


@dataclass(frozen=True, slots=True)
class BreakerTrip:
    """A breaker that tripped on this evaluation, with the pause detail it writes."""

    reason: BreakerReason
    detail: str


def deployment_mode(aggregate: PortfolioAggregate) -> DeploymentMode:
    """The deployment mode every sleeve of this portfolio runs in."""
    return DeploymentMode.LIVE if aggregate.portfolio.mode == "live" else DeploymentMode.PAPER


def members(deployments: Iterable[Deployment], portfolio_id: UUID) -> tuple[Deployment, ...]:
    """Every deployment tagged with this portfolio, newest first."""
    tagged = [item for item in deployments if item.portfolio_id == portfolio_id]
    tagged.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
    return tuple(tagged)


def sleeve_books(aggregate: PortfolioAggregate, tagged: Sequence[Deployment]) -> PortfolioBooks:
    """Match each sleeve to its book (occupied first, else newest) and derive the state."""
    capital = aggregate.portfolio.capital_quote
    books: list[SleeveBook] = []
    strategy_ids = set()
    for view in aggregate.sleeves:
        strategy_ids.add(view.sleeve.strategy_id)
        books.append(
            SleeveBook(
                view=view,
                target_capital=Decimal(sleeve_capital(capital, view.sleeve.weight_fraction)),
                deployment=_current_book(tagged, view.sleeve.strategy_id),
            )
        )
    detached = tuple(
        item
        for item in tagged
        if item.strategy_id not in strategy_ids and occupies_running_slot(item)
    )
    return PortfolioBooks(
        sleeves=tuple(books), detached=detached, state=_state(tuple(books), tagged)
    )


def _current_book(tagged: Sequence[Deployment], strategy_id: UUID) -> Deployment | None:
    """The sleeve's running or paused book, else its newest stopped one."""
    mine = [item for item in tagged if item.strategy_id == strategy_id]
    occupied = [item for item in mine if occupies_running_slot(item)]
    if occupied:
        return occupied[0]
    return mine[0] if mine else None


def _state(books: tuple[SleeveBook, ...], tagged: Sequence[Deployment]) -> PortfolioDeploymentState:
    """Summarize the sleeves' books as one portfolio state."""
    if not tagged:
        return "not_deployed"
    statuses = [book.deployment.status for book in books if book.deployment is not None]
    running = sum(1 for status in statuses if status is DeploymentStatus.RUNNING)
    paused = sum(1 for status in statuses if status is DeploymentStatus.PAUSED)
    if running == 0 and paused == 0:
        return "stopped"
    if running == len(books):
        return "running"
    if running == 0:
        return "paused"
    return "partially_running"


def occupied_members(tagged: Iterable[Deployment]) -> tuple[Deployment, ...]:
    """Tagged books that are running or paused."""
    return tuple(item for item in tagged if occupies_running_slot(item))


def run_members(
    tagged: Iterable[Deployment], runtime: PortfolioRuntimeState
) -> tuple[Deployment, ...]:
    """Books of the current run: created at or after the run started (all, if unknown)."""
    started = runtime.run_started_at
    return tuple(item for item in tagged if started is None or item.created_at >= started)


def starting_equity(deployment: Deployment) -> Decimal:
    """The equity a book's net PnL is measured from (paper: its starting cash)."""
    if deployment.initial_equity is not None:
        return deployment.initial_equity
    if deployment.paper_starting_cash is not None:
        return deployment.paper_starting_cash
    return _ZERO


def net_pnl(deployment: Deployment) -> Decimal:
    """Persisted, bar-close-marked equity minus starting equity (0 before the first bar)."""
    if deployment.performance_equity is None:
        return _ZERO
    return deployment.performance_equity - starting_equity(deployment)


def run_equity(aggregate: PortfolioAggregate, books: Iterable[Deployment]) -> Decimal:
    """Portfolio capital plus the net PnL of every book in the run."""
    return Decimal(aggregate.portfolio.capital_quote) + sum(
        (net_pnl(item) for item in books), _ZERO
    )


def utc_day_start(moment: datetime) -> datetime:
    """00:00 UTC on the calendar day of ``moment``."""
    as_utc = moment.astimezone(UTC)
    return datetime(as_utc.year, as_utc.month, as_utc.day, tzinfo=UTC)


def begin_run(
    runtime: PortfolioRuntimeState, *, equity: Decimal, now: datetime
) -> PortfolioRuntimeState:
    """Start a new run: re-baseline the day open and the high-water mark at ``equity``."""
    return replace(
        runtime,
        run_started_at=now,
        day_open_equity=equity,
        day_open_at=utc_day_start(now),
        high_water_mark_equity=equity,
        last_equity=equity,
        last_evaluated_at=now,
    )


def roll_baselines(
    runtime: PortfolioRuntimeState, *, equity: Decimal, now: datetime
) -> PortfolioRuntimeState:
    """Record ``equity``: open a new UTC day when one began and raise the high-water mark."""
    day_start = utc_day_start(now)
    day_open = runtime.day_open_equity
    day_at = runtime.day_open_at
    if day_open is None or day_at is None or day_at < day_start:
        day_open, day_at = equity, day_start
    peak = runtime.high_water_mark_equity
    peak = equity if peak is None else max(peak, equity)
    return replace(
        runtime,
        day_open_equity=day_open,
        day_open_at=day_at,
        high_water_mark_equity=peak,
        last_equity=equity,
        last_evaluated_at=now,
    )


def drawdown_fraction(runtime: PortfolioRuntimeState) -> Decimal | None:
    """Drawdown of the last equity from the run's high-water mark (None before any mark)."""
    peak = runtime.high_water_mark_equity
    equity = runtime.last_equity
    if peak is None or equity is None or peak <= 0:
        return None
    return max(_ZERO, (peak - equity) / peak)


def daily_pnl(runtime: PortfolioRuntimeState) -> Decimal | None:
    """Equity change since the UTC day opened (negative is a loss)."""
    if runtime.last_equity is None or runtime.day_open_equity is None:
        return None
    return runtime.last_equity - runtime.day_open_equity


def tripped_breaker(
    aggregate: PortfolioAggregate, runtime: PortfolioRuntimeState
) -> BreakerTrip | None:
    """The first breaker the recorded equity trips: the daily loss stop, then drawdown."""
    limits = aggregate.portfolio.limits
    quote = aggregate.portfolio.quote_currency
    change = daily_pnl(runtime)
    if limits.daily_loss_quote is not None and change is not None:
        limit = Decimal(limits.daily_loss_quote)
        if -change >= limit:
            return BreakerTrip(
                reason="PORTFOLIO_DAILY_LOSS_STOP",
                detail=(
                    f"The portfolio lost {quote_text(_money(-change), quote)} since the UTC "
                    f"day opened; its daily loss stop is {quote_text(limit, quote)}."
                ),
            )
    drawdown = drawdown_fraction(runtime)
    if limits.max_drawdown_fraction is not None and drawdown is not None:
        stop = Decimal(limits.max_drawdown_fraction)
        if drawdown >= stop:
            return BreakerTrip(
                reason="PORTFOLIO_DRAWDOWN_STOP",
                detail=(
                    f"Portfolio equity is {percent_text(_fraction(drawdown))} below its peak "
                    f"of {quote_text(_money(runtime.high_water_mark_equity or _ZERO), quote)}; "
                    f"its drawdown stop is {percent_text(stop)}."
                ),
            )
    return None


def risk_book(aggregate: PortfolioAggregate, runtime: PortfolioRuntimeState) -> PortfolioRiskBook:
    """The entry gate's view of one portfolio's limits and breaker latch."""
    portfolio = aggregate.portfolio
    reason = None if runtime.breaker_reason is None else RiskReasonCode(runtime.breaker_reason)
    return PortfolioRiskBook(
        portfolio_id=portfolio.portfolio_id,
        capital=Decimal(portfolio.capital_quote),
        max_total_exposure_fraction=Decimal(portfolio.limits.max_total_exposure_fraction),
        max_per_asset_fraction=Decimal(portfolio.limits.max_per_asset_fraction),
        live=portfolio.mode == "live",
        breaker_reason=reason,
    )


def _money(value: Decimal) -> Decimal:
    """Quote amounts in journal text keep two decimals."""
    return value.quantize(Decimal("0.01"))


def _fraction(value: Decimal) -> str:
    """A fraction rounded to 0.01% for display."""
    return canonical_decimal(value.quantize(Decimal("0.0001")))
