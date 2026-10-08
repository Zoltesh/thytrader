"""Continuously evaluate deployed strategies against closed candles."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.alerts.supervision import worker_book_failure_finding
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.decision_journal import (
    PRUNE_INTERVAL,
    decision_journal_scope,
    prune_decisions,
)
from thytrader.execution.ids import utc_now
from thytrader.execution.leases import RevisionFencedStore, acquire_worker_lease
from thytrader.execution.models import DeploymentKind, DeploymentMode, DeploymentStatus
from thytrader.execution_worker.discretionary_step import _process_discretionary
from thytrader.execution_worker.portfolio_supervisor import supervise_portfolios
from thytrader.execution_worker.ports import QuoteBalanceReader, _logger
from thytrader.execution_worker.strategy_step import (
    _advance_strategy,
    _advance_strategy_ready,
    _journaled_bar,
    _process_stopped,
    _stopped_strategy_definition,
    _strategy_definition,
)
from thytrader.execution_worker.supervision import (
    _MISSING_DECISION_CANDLES,
    USER_FEED_PAUSE_DETAIL,
    _pause_five_minute_live_if_feed_down,
    _pause_repeatedly_failing_books,
    _supervise_safety,
    _supervise_warming_window,
    _user_feed_connected,
)
from thytrader.execution_worker.windows import (
    _closed_htf_window,
    _closed_indicator_timeframe_windows,
    _closed_reference_windows,
    _closed_window,
    _closed_window_for,
    _required_clock_warmup_bars,
    _shared_clock_union_warmup,
    _signal_exit_windows,
    htf_coverage_ready,
    new_closed_bars,
)
from thytrader.fleet_control.admission import refresh_process_entry_inhibition
from thytrader.risk.accounting_evidence import risk_market_data_scope
from thytrader.risk.exposure import daily_loss_snapshots
from thytrader.risk.portfolio_scope import portfolio_risk_scope
from thytrader.risk.store import load_effective_policy

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from thytrader.alerts.models import SupervisionFinding
    from thytrader.alerts.service import AlertService
    from thytrader.audit_events import AuditEventStore
    from thytrader.execution.broker import Broker
    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.models import Deployment, DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.execution_worker.venue import ExecutionVenue
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.portfolios.store import PortfolioRuntimeStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.settings_yaml import SettingsStore
    from thytrader.strategies.snapshots import StrategySnapshotStore

__all__ = [
    "USER_FEED_PAUSE_DETAIL",
    "_MISSING_DECISION_CANDLES",
    "QuoteBalanceReader",
    "_advance_strategy",
    "_advance_strategy_ready",
    "_closed_htf_window",
    "_closed_indicator_timeframe_windows",
    "_closed_reference_windows",
    "_closed_window",
    "_closed_window_for",
    "_journaled_bar",
    "_pause_five_minute_live_if_feed_down",
    "_pause_repeatedly_failing_books",
    "_process_discretionary",
    "_process_one",
    "_process_stopped",
    "_prune_decisions_when_due",
    "_required_clock_warmup_bars",
    "_risk_snapshots",
    "_run_cycle",
    "_shared_clock_union_warmup",
    "_signal_exit_windows",
    "_supervise_safety",
    "_supervise_warming_window",
    "_user_feed_connected",
    "htf_coverage_ready",
    "new_closed_bars",
    "run_execution_worker",
]


async def run_execution_worker(
    stop_requested: asyncio.Event,
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    interval_seconds: int,
    on_readiness_changed: Callable[[bool], None] | None = None,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    risk_store: RiskPolicyStore | None = None,
    user_feed_store: UserOrderFeedStateStore | None = None,
    wake_requested: asyncio.Event | None = None,
    memory_store: ExperientialMemoryStore | None = None,
    settings_store: SettingsStore | None = None,
    venue_provider: Callable[[], ExecutionVenue] | None = None,
    audit_store: AuditEventStore | None = None,
    decision_store: DecisionJournalStore | None = None,
    portfolio_store: PortfolioRuntimeStore | None = None,
    alert_service: AlertService | None = None,
) -> None:
    """Poll running deployments until shutdown.

    When ``venue_provider`` is given it overrides ``market_data``, ``live_broker``,
    and ``quote_reader``. It is read once at the start of every cycle, so a
    credential hot-swap takes effect between cycles and never mid-cycle.
    ``audit_store`` is bound for execution audit events (rejected/unconfirmed
    submits, recovery, user-feed pause transitions) for the whole cycle.
    ``decision_store`` journals one decision per evaluated bar and is pruned on a
    bounded schedule; journal failures never stop or alter a cycle (ADR 0087).
    ``alert_service`` records durable safety supervision alerts each cycle
    (ADR 0115); alert-store failures never stop or alter a cycle either.
    """
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    next_prune_at = datetime.now(UTC)
    try:
        while not stop_requested.is_set():
            if heartbeat_store is not None:
                await heartbeat_store.touch("execution_worker", datetime.now(UTC))
            cycle_market_data, cycle_live_broker, cycle_quote_reader = (
                market_data,
                live_broker,
                quote_reader,
            )
            if venue_provider is not None:
                venue = venue_provider()
                cycle_market_data = venue.market_data
                cycle_live_broker = venue.live_broker
                cycle_quote_reader = venue.quote_reader
            wait_seconds = (
                settings_store.current().execution_worker_interval_seconds
                if settings_store is not None
                else interval_seconds
            )
            with execution_audit_scope(audit_store), decision_journal_scope(decision_store):
                await _run_cycle(
                    store=store,
                    publication_store=publication_store,
                    market_data=cycle_market_data,
                    paper_broker=paper_broker,
                    live_broker=cycle_live_broker,
                    quote_reader=cycle_quote_reader,
                    risk_store=risk_store,
                    user_feed_store=user_feed_store,
                    memory_store=memory_store,
                    portfolio_store=portfolio_store,
                    alert_service=alert_service,
                    worker_interval_seconds=wait_seconds,
                )
                next_prune_at = await _prune_decisions_when_due(decision_store, next_prune_at)
            if wake_requested is not None:
                wake_requested.clear()
            await _await_next_cycle(
                stop_requested, wake_requested=wake_requested, interval_seconds=wait_seconds
            )
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


async def _prune_decisions_when_due(
    decision_store: DecisionJournalStore | None, next_prune_at: datetime
) -> datetime:
    """Run one bounded decision-journal retention pass at startup and every 6 hours."""
    now = datetime.now(UTC)
    if decision_store is None or now < next_prune_at:
        return next_prune_at
    await prune_decisions(decision_store, now=now)
    return now + PRUNE_INTERVAL


async def _await_next_cycle(
    stop_requested: asyncio.Event,
    *,
    wake_requested: asyncio.Event | None,
    interval_seconds: int,
) -> None:
    """Sleep until the poll interval, a user-feed nudge, or shutdown."""
    if wake_requested is None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)
        return
    wake = asyncio.create_task(wake_requested.wait())
    stop = asyncio.create_task(stop_requested.wait())
    _done, pending = await asyncio.wait(
        {wake, stop}, timeout=interval_seconds, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()


async def _run_cycle(
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_store: RiskPolicyStore | None,
    user_feed_store: UserOrderFeedStateStore | None = None,
    memory_store: ExperientialMemoryStore | None = None,
    portfolio_store: PortfolioRuntimeStore | None = None,
    alert_service: AlertService | None = None,
    worker_interval_seconds: int = 30,
) -> None:
    """Process occupied deployments once, refreshing occupancy after each for the entry gate.

    Deployed portfolios are supervised first (capital sync, equity, breakers; ADR 0091),
    and each sleeve book is processed with its portfolio's limits bound for the gate.
    Safety supervision (ADR 0115) runs after the book loop, decoupled from
    successful signal evaluation: cycle failures feed the durable alert feed, and
    a book that fails too many consecutive cycles has its entries paused while
    exits and reconciliation keep running.
    """
    cycle_started_at = utc_now()
    await refresh_process_entry_inhibition(store)
    policy = (await load_effective_policy(risk_store)).definition
    deployments = await store.list_deployments()
    books = await supervise_portfolios(
        store=store, portfolios=portfolio_store, deployments=deployments, now=utc_now()
    )
    portfolio = await _risk_snapshots(store, deployments)
    cycle_failures: list[SupervisionFinding] = []
    for deployment in deployments:
        if deployment.status not in {
            DeploymentStatus.RUNNING,
            DeploymentStatus.PAUSED,
            DeploymentStatus.STOPPED,
        }:
            continue
        book = None if deployment.portfolio_id is None else books.get(deployment.portfolio_id)
        try:
            with portfolio_risk_scope(book):
                await _process_one(
                    deployment_id=deployment.id,
                    store=store,
                    publication_store=publication_store,
                    market_data=market_data,
                    paper_broker=paper_broker,
                    live_broker=live_broker,
                    quote_reader=quote_reader,
                    risk_policy=policy,
                    portfolio=portfolio,
                    user_feed_store=user_feed_store,
                    memory_store=memory_store,
                )
        except (RuntimeError, ValueError, TypeError, OSError) as error:
            _logger.exception("execution_cycle_failed deployment_id=%s", deployment.id)
            cycle_failures.append(
                worker_book_failure_finding(deployment, error_type=type(error).__name__)
            )
        portfolio = await _risk_snapshots(store, deployments)
    await _supervise_safety(
        alert_service=alert_service,
        store=store,
        market_data=market_data,
        deployments=deployments,
        cycle_failures=cycle_failures,
        worker_interval_seconds=worker_interval_seconds,
        observed_at=cycle_started_at,
    )


async def _process_one(
    *,
    deployment_id: UUID,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    user_feed_store: UserOrderFeedStateStore | None,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Load evidence and advance one deployment through newly closed bars."""
    leased = await acquire_worker_lease(store, deployment_id)
    if leased is None:
        return
    snapshot = await store.get_deployment(deployment_id)
    store = RevisionFencedStore(store, snapshot.deployment.id, snapshot.deployment.revision)
    strategy = None
    if snapshot.deployment.status is DeploymentStatus.STOPPED:
        strategy = await _stopped_strategy_definition(
            snapshot, store=store, publication_store=publication_store
        )
        snapshot = await store.get_deployment(deployment_id)
        await _process_stopped(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
        )
        return
    if snapshot.deployment.kind is not DeploymentKind.DISCRETIONARY:
        strategy = await _strategy_definition(
            snapshot, store=store, publication_store=publication_store
        )
    if snapshot.deployment.kind is DeploymentKind.DISCRETIONARY:
        with risk_market_data_scope(market_data):
            await _process_discretionary(
                snapshot,
                store=store,
                market_data=market_data,
                paper_broker=paper_broker,
                live_broker=live_broker,
                quote_reader=quote_reader,
                user_feed_store=user_feed_store,
                risk_policy=risk_policy,
                memory_store=memory_store,
            )
        return
    if strategy is None:
        return
    with risk_market_data_scope(market_data):
        await _advance_strategy(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            risk_policy=risk_policy,
            portfolio=portfolio,
            user_feed_store=user_feed_store,
            memory_store=memory_store,
        )


async def _risk_snapshots(
    store: ExecutionStore,
    deployments: Sequence[Deployment],
) -> tuple[DeploymentSnapshot, ...]:
    """Load snapshots used by the entry gate, including stopped flat loss evidence.

    Exposure and rate limits re-filter to risk-bearing books. Daily loss and latches
    need stopped flat rows, so this set must not drop them.
    """
    loaded = [await store.get_deployment(item.id) for item in deployments]
    paper = daily_loss_snapshots(loaded, DeploymentMode.PAPER)
    live = daily_loss_snapshots(loaded, DeploymentMode.LIVE)
    return paper + live
