"""Per-cycle supervision of deployed portfolios for the execution worker (ADR 0091).

Before the worker processes any book it supervises every portfolio with a running or
paused sleeve:

1. keep each sleeve's allocated capital equal to its weight times the portfolio's capital
   (a rebalance or capital change binds the sleeve's next entries, paper and live);
2. record the run's equity (capital plus each run book's persisted net PnL), rolling the
   UTC day open and raising the high-water mark. While a run book's recorded equity is
   unresolved, the baselines are held and no new trip is evaluated: unknown is not zero PnL;
3. trip the portfolio breakers: the daily loss stop (``daily_loss_quote``) and the
   drawdown stop (``max_drawdown_fraction``). A trip latches until an operator reset,
   pauses every running sleeve with a ``PORTFOLIO_*_STOP:`` detail, and is journaled. While
   latched, any sleeve resumed behind the portfolio's back is paused again;
4. hand the entry gate one :class:`PortfolioRiskBook` per portfolio.

Failures never stop the cycle: a portfolio whose state cannot be read gets no book, and
the entry gate then refuses its sleeves' entries (fail closed).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

from thytrader.portfolios.deployment import (
    BreakerTrip,
    members,
    occupied_members,
    risk_book,
    roll_baselines,
    run_equity,
    run_members,
    tripped_breaker,
)
from thytrader.portfolios.models import (
    JournalDetail,
    JournalEntry,
    MutationContext,
    PortfolioError,
    PortfolioRuntimeState,
    utc_millisecond,
)
from thytrader.portfolios.rules import journal_entry, sleeve_capital
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    LifecycleCommand,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.portfolios.models import PortfolioAggregate, PortfolioRuntimeView
    from thytrader.portfolios.store import PortfolioRuntimeStore
    from thytrader.risk.gate import PortfolioRiskBook
    from thytrader.trading.models import Deployment
    from thytrader.trading.store import ExecutionStore

_logger = logging.getLogger(__name__)
_HEARTBEAT = timedelta(minutes=1)


async def supervise_portfolios(
    *,
    store: ExecutionStore,
    portfolios: PortfolioRuntimeStore | None,
    deployments: Sequence[Deployment],
    now: datetime,
) -> dict[UUID, PortfolioRiskBook]:
    """Supervise every portfolio a deployment is tagged with and return their gate books."""
    portfolio_ids = sorted(
        {item.portfolio_id for item in deployments if item.portfolio_id is not None}, key=str
    )
    if not portfolio_ids or portfolios is None:
        return {}
    try:
        views = await portfolios.runtime_views(portfolio_ids)
    except PortfolioError as error:
        _logger.warning("portfolio_supervision_unavailable error=%s", type(error).__name__)
        return {}
    books: dict[UUID, PortfolioRiskBook] = {}
    for view in views:
        portfolio_id = view.aggregate.portfolio.portfolio_id
        try:
            runtime = await _supervise_one(
                view, store=store, portfolios=portfolios, deployments=deployments, now=now
            )
        except (PortfolioError, ExecutionStoreError, ExecutionConflictError) as error:
            _logger.warning(
                "portfolio_supervision_failed portfolio_id=%s error=%s",
                portfolio_id,
                type(error).__name__,
            )
            continue
        books[portfolio_id] = risk_book(view.aggregate, runtime)
    return books


async def _supervise_one(
    view: PortfolioRuntimeView,
    *,
    store: ExecutionStore,
    portfolios: PortfolioRuntimeStore,
    deployments: Sequence[Deployment],
    now: datetime,
) -> PortfolioRuntimeState:
    """Sync capital, record equity, trip or replay the breaker; return the gate's state."""
    aggregate = view.aggregate
    runtime = view.runtime
    tagged = members(deployments, aggregate.portfolio.portfolio_id)
    occupied = occupied_members(tagged)
    if not occupied:
        return runtime
    await _sync_allocations(aggregate, occupied, store=store, now=now)
    running = tuple(item for item in occupied if item.status is DeploymentStatus.RUNNING)
    run = run_members(tagged, runtime)
    if await _equity_unresolved(run, store=store):
        return await _enforce_latch(runtime, running, store=store, now=now)
    recorded = roll_baselines(runtime, equity=run_equity(aggregate, run), now=now)
    trip = None if runtime.breaker_latched else tripped_breaker(aggregate, recorded)
    journal: tuple[JournalEntry, ...] = ()
    if trip is not None:
        recorded = replace(
            recorded,
            breaker_reason=trip.reason,
            breaker_detail=trip.detail[:500],
            breaker_latched_at=now,
        )
        journal = (_trip_entry(aggregate, trip, running, now=now),)
    if trip is None and not _baselines_moved(runtime, recorded, now=now):
        state = runtime
    else:
        written = await portfolios.write_runtime(
            recorded, expected_revision=runtime.revision, journal=journal
        )
        state = written or (recorded if trip is not None else runtime)
    return await _enforce_latch(state, running, store=store, now=now)


async def _equity_unresolved(run: Sequence[Deployment], *, store: ExecutionStore) -> bool:
    """Whether a run book's null equity is unresolved accounting rather than zero PnL.

    :func:`net_pnl` reads a null persisted equity as zero, which is right before a book's
    first mark but not once the ledger refused to certify its accounting. This is the same
    ledger check the portfolio views use to withhold run equity. An unreadable book counts
    as unresolved.
    """
    for book in run:
        if book.performance_equity is not None:
            continue
        try:
            snapshot = await store.get_deployment(book.id)
        except ExecutionStoreError:
            return True
        if not ledger_from_snapshot(snapshot).accounting_complete:
            return True
    return False


async def _enforce_latch(
    state: PortfolioRuntimeState,
    running: Sequence[Deployment],
    *,
    store: ExecutionStore,
    now: datetime,
) -> PortfolioRuntimeState:
    """Pause every running sleeve while the breaker is latched; return the gate's state."""
    if state.breaker_reason is not None and running:
        detail = f"{state.breaker_reason}: {state.breaker_detail or 'portfolio breaker latched'}"
        await _pause_running(running, detail=detail[:2000], store=store, now=now)
    return state


def _baselines_moved(
    runtime: PortfolioRuntimeState, recorded: PortfolioRuntimeState, *, now: datetime
) -> bool:
    """Whether the recorded equity or baselines changed, or the last record is a minute old.

    Skipping unchanged writes keeps the runtime row (and its compare-and-set revision) quiet
    between bars, so an operator reset rarely races a no-op worker write.
    """
    if (
        runtime.day_open_equity,
        runtime.day_open_at,
        runtime.high_water_mark_equity,
        runtime.last_equity,
    ) != (
        recorded.day_open_equity,
        recorded.day_open_at,
        recorded.high_water_mark_equity,
        recorded.last_equity,
    ):
        return True
    last = runtime.last_evaluated_at
    return last is None or now - last >= _HEARTBEAT


async def _sync_allocations(
    aggregate: PortfolioAggregate,
    occupied: Sequence[Deployment],
    *,
    store: ExecutionStore,
    now: datetime,
) -> None:
    """Set each sleeve book's allocated capital to its current weight times capital."""
    weights = {view.sleeve.strategy_id: view.sleeve.weight_fraction for view in aggregate.sleeves}
    for item in occupied:
        weight = weights.get(item.strategy_id) if item.strategy_id is not None else None
        if weight is None:
            continue
        target = Decimal(sleeve_capital(aggregate.portfolio.capital_quote, weight))
        if item.allocated_capital == target:
            continue
        try:
            await store.save_deployment(
                replace(item, allocated_capital=target, updated_at=now),
                expected_revision=item.revision,
            )
        except ExecutionConflictError:
            continue


async def _pause_running(
    running: Sequence[Deployment], *, detail: str, store: ExecutionStore, now: datetime
) -> None:
    """Pause each running sleeve book (exits and protection continue on paused books)."""
    for item in running:
        paused = with_runtime(
            item,
            updated_at=now,
            status=DeploymentStatus.PAUSED,
            lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
            mismatch_detail=detail,
        )
        try:
            await store.save_deployment(paused, expected_revision=item.revision)
        except ExecutionConflictError:
            # Changed since the cycle read it; the latch replays next cycle and the gate
            # refuses its entries meanwhile.
            continue


def _trip_entry(
    aggregate: PortfolioAggregate,
    trip: BreakerTrip,
    running: Sequence[Deployment],
    *,
    now: datetime,
) -> JournalEntry:
    """The ``breaker_tripped`` journal entry (actor system)."""
    label = "daily loss stop" if trip.reason == "PORTFOLIO_DAILY_LOSS_STOP" else "drawdown stop"
    return journal_entry(
        aggregate.portfolio.portfolio_id,
        kind="breaker_tripped",
        context=MutationContext(actor="system", channel="system", occurred_at=utc_millisecond(now)),
        summary=(
            f"The portfolio {label} tripped and paused {len(running)} sleeve"
            f"{'' if len(running) == 1 else 's'}; it stays latched until an operator resets it. "
            f"{trip.detail}"
        ),
        revision=aggregate.portfolio.revision,
        detail=JournalDetail(
            reason="breaker",
            reason_code=trip.reason,
            deployment_ids=tuple(item.id for item in running),
        ),
    )
