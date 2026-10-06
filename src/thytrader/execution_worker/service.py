"""Continuously evaluate deployed strategies against closed candles."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
import logging
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from thytrader.alerts.models import AlertCode, SupervisionFinding
from thytrader.alerts.store import AlertStoreError
from thytrader.alerts.supervision import gather_safety_findings, worker_book_failure_finding
from thytrader.exchanges.ws.market_feed import DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
from thytrader.execution.audit_scope import execution_audit_scope, record_execution_audit
from thytrader.execution.candle_wait import newest_bar_settling
from thytrader.execution.capital import apply_venue_quote
from thytrader.execution.decision_journal import (
    PRUNE_INTERVAL,
    decision_journal_scope,
    observe_bar,
    prune_decisions,
    record_bar_decision,
    record_gate_skip,
)
from thytrader.execution.decision_scope import note_reference_gate
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.discretionary import process_discretionary_bar
from thytrader.execution.freshness import signal_still_valid
from thytrader.execution.geometry import base_currency, entry_bar_bucket
from thytrader.execution.ids import utc_now
from thytrader.execution.leases import RevisionFencedStore, acquire_worker_lease
from thytrader.execution.loop import (
    maintain_discretionary_protection,
    maintain_open_inventory,
    process_closed_bar,
)
from thytrader.execution.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    LifecycleCommand,
    with_runtime,
)
from thytrader.execution.overlay import InstrumentScopedStore
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.references import ReferenceGate, reference_gate
from thytrader.execution.stopped import (
    load_verified_exit_context,
    stopped_product_ids,
    supervise_stopped_deployment,
)
from thytrader.execution.trade_reason_scope import (
    discretionary_trade_reason_scope,
    strategy_trade_reason_scope,
    trade_reason_scope,
)
from thytrader.execution.user_feed_state import UserOrderFeedState, UserOrderFeedUnavailableError
from thytrader.execution_worker.portfolio_supervisor import supervise_portfolios
from thytrader.fleet_control.admission import refresh_process_entry_inhibition
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.persistence.audit_events import AuditEventOutcome
from thytrader.research.models import warmup_starts_at
from thytrader.research.multi_timeframe import closed_bar_required_coverage, ltf_close
from thytrader.risk.exposure import risk_bearing_snapshots
from thytrader.risk.portfolio_scope import portfolio_risk_scope
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    extra_indicator_timeframes,
    lockstep_product_ids,
    reference_data_requirements,
    signal_exit_condition,
    strategy_indicator_operands,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from decimal import Decimal

    from thytrader.alerts.service import AlertApplication, AlertService
    from thytrader.alerts.supervision import ClosedCandleReader
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.execution.broker import Broker
    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.models import Deployment, DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.execution_worker.venue import ExecutionVenue
    from thytrader.market_data.models import Candle, CandleRangeReport, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.persistence.audit_events import AuditEventStore
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.portfolios.store import PortfolioRuntimeStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.settings_yaml import SettingsStore
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshotStore

_logger = logging.getLogger(__name__)


class QuoteBalanceReader(Protocol):
    """Read quote cash for live sizing."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return non-empty exchange balances."""
        ...


class LiveFeeProfileReader(Protocol):
    """Fetch the venue maker/taker tier used to size live entries."""

    async def get_fee_profile(self) -> FeeProfile:
        """Return the latest Coinbase fee profile."""
        ...


def _latest_due_bar_may_enter(candle: Candle, *, timeframe: str, is_latest: bool) -> bool:
    """True when this recovered close is the newest due bar and still within max age."""
    return is_latest and signal_still_valid(
        candle=candle,
        timeframe=timeframe,
        now=utc_now(),
        current_quote=candle.close,
    )


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
    )


async def _supervise_safety(
    *,
    alert_service: AlertService | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    deployments: tuple[Deployment, ...],
    cycle_failures: list[SupervisionFinding],
    worker_interval_seconds: int,
) -> None:
    """Record durable safety alerts and pause repeatedly failing books (ADR 0115).

    Supervision failures never fail the worker cycle: an unavailable alert store
    is logged and skipped, and the pause path only touches RUNNING books whose
    consecutive failure count crossed the configured threshold. Pausing blocks
    new entries only; the book keeps being processed for exits, protection, and
    reconciliation, and user pauses and latches are never resumed or bypassed.
    """
    if alert_service is None:
        return
    try:
        findings = await gather_safety_findings(
            deployments=deployments,
            snapshots=store,
            closed_candles=_supervision_candle_reader(market_data),
            now=utc_now(),
            thresholds=alert_service.thresholds,
            worker_interval_seconds=worker_interval_seconds,
        )
        application = await alert_service.apply((*findings, *cycle_failures), now=utc_now())
    except AlertStoreError as error:
        _logger.warning("safety_supervision_skipped type=%s detail=%s", type(error).__name__, error)
        return
    await _pause_repeatedly_failing_books(
        store,
        application,
        deployments=deployments,
        consecutive_failure_cycles=alert_service.thresholds.consecutive_failure_cycles,
    )


def _supervision_candle_reader(market_data: MarketDataService) -> ClosedCandleReader:
    """Adapt the worker's closed-window fetch to best-effort supervision reads."""

    async def read(product_id: str, timeframe: str, deploy_anchor: datetime) -> tuple[Candle, ...]:
        _product, candles, _expected = await _closed_window_for(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=3,
            deploy_anchor=deploy_anchor,
        )
        return candles

    return read


def _may_pause_for_failures(
    deployment: Deployment, occurrences: int, consecutive_failure_cycles: int
) -> bool:
    """True only for a running book whose entries can be paused without hiding another latch.

    User pauses, breaker latches, and an existing mismatch are left untouched.
    Supervision never resumes a book.
    """
    return not (
        occurrences < consecutive_failure_cycles
        or deployment.status is not DeploymentStatus.RUNNING
        or deployment.lifecycle_command is not LifecycleCommand.NONE
        or bool(deployment.mismatch_detail)
        or deployment.daily_loss_latched
        or deployment.drawdown_latched
    )


async def _pause_repeatedly_failing_books(
    store: ExecutionStore,
    application: AlertApplication,
    *,
    deployments: tuple[Deployment, ...],
    consecutive_failure_cycles: int,
) -> None:
    """Pause entries for books whose consecutive cycle failures crossed the threshold."""
    by_id = {deployment.id: deployment for deployment in deployments}
    for change in application.changes:
        alert = change.alert
        if alert.code is not AlertCode.WORKER_BOOK_FAILURES:
            continue
        try:
            deployment_id = UUID(alert.subject)
        except ValueError:
            continue
        deployment = by_id.get(deployment_id)
        if deployment is None:
            continue
        if not _may_pause_for_failures(deployment, alert.occurrences, consecutive_failure_cycles):
            continue
        detail = (
            f"WORKER_CONSECUTIVE_FAILURES: entries paused after {alert.occurrences} failed "
            "supervision cycles; exits and reconciliation continue. Operator review required; "
            "resume is manual."
        )
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
        await store.save_deployment(paused)
        await record_execution_audit(
            action="worker_consecutive_failures_pause",
            outcome=AuditEventOutcome.FAILURE,
            detail=(
                f"deployment_id={deployment.id}: paused new entries after "
                f"{alert.occurrences} consecutive failed cycles (ADR 0115)."
            ),
            product_id=deployment.product_id,
        )


async def _process_stopped(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> None:
    """Apply flatten or managed-shutdown to every stopped book without dropping risk."""

    async def _signal_windows(
        strategy: StrategyDefinition, *, product_id: str, deploy_anchor: datetime
    ) -> tuple[tuple[Candle, ...], dict[str, tuple[Candle, ...]], dict[str, tuple[Candle, ...]]]:
        """Load one stopped product's signal-exit clocks, or nothing on a gap."""
        htf, extra = await _signal_exit_windows(
            market_data, strategy, product_id=product_id, deploy_anchor=deploy_anchor
        )
        references = (
            {}
            if signal_exit_condition(strategy.exits) is None
            else await _closed_reference_windows(market_data, strategy, deploy_anchor=deploy_anchor)
        )
        return htf, extra, references

    await supervise_stopped_deployment(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
        load_closed_window=_closed_window_for,
        load_signal_windows=_signal_windows,
        journal_strategy_bar=_journal_stopped_bar,
    )


_MISSING_DECISION_CANDLES = "Market-data window is gapped or missing the latest closed bar."


async def _journal_stopped_bar(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: tuple[Candle, ...],
    advance: Callable[[], Awaitable[DeploymentSnapshot]],
    require_activity: bool,
) -> None:
    """Journal one stopped strategy bar without allowing a new entry."""
    await _journaled_bar(
        snapshot,
        strategy=strategy,
        product_id=product.product_id,
        candle=candles[-1],
        allow_new_entries=False,
        require_activity=require_activity,
        advance=advance,
    )


async def _supervise_without_decision_candles(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    product: MarketProduct,
    expected_last: datetime,
    feed_paused: bool,
) -> None:
    """Pause new entries when the decision window is empty, and still reconcile live orders.

    No candle is fabricated and no market order is priced from an invented close.
    Another covered product that still has its own verified candles keeps protection.
    """
    await record_gate_skip(
        snapshot=snapshot,
        strategy=strategy,
        product_ids=lockstep_product_ids(strategy),
        bar_starts_at=expected_last,
        reason=DecisionSkipReason.DATA_GAP,
        detail=_MISSING_DECISION_CANDLES,
    )
    if feed_paused:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=lockstep_product_ids(strategy),
            bar_starts_at=expected_last,
            reason=DecisionSkipReason.USER_FEED_GATE,
            detail=USER_FEED_PAUSE_DETAIL,
        )
    await _pause_running_for_missing_candles(snapshot, store=store, live_broker=live_broker)
    current = await store.get_deployment(snapshot.deployment.id)
    if current.deployment.mode is DeploymentMode.LIVE and live_broker is not None:
        current = await reconcile_open_orders(
            current,
            broker=live_broker,
            store=store,
            product_id=product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
    await _maintain_verified_books(
        current,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
    )


async def _pause_running_for_missing_candles(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    live_broker: Broker | None,
) -> None:
    """Pause a running book for missing candles without clearing another pause."""
    current = await store.get_deployment(snapshot.deployment.id)
    if current.deployment.status is not DeploymentStatus.RUNNING:
        return
    detail = _MISSING_DECISION_CANDLES
    if current.deployment.mode is DeploymentMode.LIVE and live_broker is None:
        detail = "Live broker is unavailable."
    await store.save_deployment(
        with_runtime(
            current.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
    )


async def _maintain_verified_books(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> None:
    """Maintain stored protection from a fresh venue context, not incomplete signal history.

    A preview can maintain persisted stop/target geometry without ATR, signal evaluation,
    advancing the decision cursor, or an invented candle. No live broker means no submit.
    """
    if snapshot.deployment.mode is DeploymentMode.LIVE and live_broker is None:
        return
    broker = _cycle_broker(snapshot.deployment, paper_broker=paper_broker, live_broker=live_broker)
    timeframe = (
        strategy.timeframe if strategy is not None else snapshot.deployment.timeframe or "1h"
    )
    for product_id in stopped_product_ids(snapshot, strategy):
        scoped = InstrumentScopedStore(store, product_id)
        focused = await scoped.get_deployment(snapshot.deployment.id)
        if focused.position is None:
            continue
        context = await load_verified_exit_context(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=3,
            deploy_anchor=snapshot.deployment.created_at,
            load_closed_window=_closed_window_for,
        )
        if context is None:
            continue
        await maintain_discretionary_protection(
            focused,
            product=context.product,
            candles=context.candles,
            broker=broker,
            store=scoped,
        )


async def _supervise_warming_window(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> None:
    """Keep observation/protection alive during prefetch without a durable data pause."""
    current = await store.get_deployment(snapshot.deployment.id)
    if current.deployment.mode is DeploymentMode.LIVE:
        if live_broker is None:
            await _pause_running_for_missing_candles(current, store=store, live_broker=None)
        else:
            current = await reconcile_open_orders(current, broker=live_broker, store=store)
    await _maintain_verified_books(
        current,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
    )


async def _hold_discretionary_without_candles(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    live_broker: Broker | None,
    feed_paused: bool,
) -> None:
    """Reconcile a discretionary live book when its clock has no candles, without exiting."""
    if not feed_paused:
        await _pause_running_for_missing_candles(snapshot, store=store, live_broker=live_broker)
    current = await store.get_deployment(snapshot.deployment.id)
    if current.deployment.mode is not DeploymentMode.LIVE or live_broker is None:
        return
    await reconcile_open_orders(
        current,
        broker=live_broker,
        store=store,
        product_id=current.deployment.product_id,
        cooldown_bars=0,
    )


async def _signal_exit_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    product_id: str | None = None,
) -> tuple[tuple[Candle, ...], dict[str, tuple[Candle, ...]]]:
    """Best-effort HTF and extra-TF windows a stopped book's exit rule reads (ADR 0093).

    Only a declared ``exits.signal_exit`` on a strategy with per-indicator extra
    timeframes needs them. A gapped window yields nothing: the exit rule then fails
    closed (no exit is invented) while the protective stop and time exit keep running.
    ``product_id`` selects a covered book; omitted, the primary instrument is used.
    """
    if signal_exit_condition(strategy.exits) is None or not extra_indicator_timeframes(strategy):
        return (), {}
    htf_candles = await _closed_htf_window(
        market_data, strategy, product_id=product_id, deploy_anchor=deploy_anchor
    )
    if htf_candles is None:
        return (), {}
    extra_candles = await _closed_indicator_timeframe_windows(
        market_data,
        strategy,
        htf_candles,
        product_id=product_id,
        deploy_anchor=deploy_anchor,
    )
    return htf_candles, extra_candles or {}


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


async def _stopped_strategy_definition(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
) -> StrategyDefinition | None:
    """Keep shutdown supervision alive when immutable strategy rules cannot be loaded."""
    if snapshot.deployment.kind is DeploymentKind.DISCRETIONARY:
        return None
    fingerprint = snapshot.deployment.strategy_fingerprint
    if fingerprint is not None:
        try:
            return (await publication_store.load(fingerprint)).definition
        except RuntimeError, OSError, ValueError, TypeError:
            pass
    if snapshot.deployment.mismatch_detail is None:
        await store.save_deployment(
            with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.STOPPED,
                mismatch_detail=(
                    "Stopped strategy snapshot is unavailable; "
                    "only stored protection is maintained."
                ),
            )
        )
    return None


async def _advance_strategy(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    user_feed_store: UserOrderFeedStateStore | None,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Advance decision bars, treating bounded cold-cache prefetch as transient."""
    try:
        await _advance_strategy_ready(
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
    except WindowCacheWarmingError:
        await _supervise_warming_window(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
        )


async def _advance_strategy_ready(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    user_feed_store: UserOrderFeedStateStore | None,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Advance only fully loaded windows; warming propagates to no-entry supervision."""
    deployment = snapshot.deployment
    product, candles, expected_last = await _closed_window(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    feed_paused = await _pause_five_minute_live_if_feed_down(
        snapshot, timeframe=strategy.timeframe, store=store, user_feed_store=user_feed_store
    )
    if not candles:
        await _supervise_without_decision_candles(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            product=product,
            expected_last=expected_last,
            feed_paused=feed_paused,
        )
        return
    if feed_paused:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=lockstep_product_ids(strategy),
            bar_starts_at=expected_last,
            reason=DecisionSkipReason.USER_FEED_GATE,
            detail=USER_FEED_PAUSE_DETAIL,
        )
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=product,
            candles=candles,
        )
        return
    interval = parse_candle_interval(strategy.timeframe)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
        allow_settling=True,
    )
    if due is None:
        detail = "Market-data window is gapped or missing the latest closed bar."
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
        await store.save_deployment(paused)
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=lockstep_product_ids(strategy),
            bar_starts_at=expected_last,
            reason=DecisionSkipReason.DATA_GAP,
            detail=detail,
        )
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=product,
            candles=candles,
        )
        return
    if not due:
        if newest_bar_settling(
            candles, expected_last_start=expected_last, bar_duration=interval.duration
        ):
            await record_gate_skip(
                snapshot=snapshot,
                strategy=strategy,
                product_ids=lockstep_product_ids(strategy),
                bar_starts_at=expected_last,
                reason=DecisionSkipReason.BAR_SETTLING,
                detail="Waiting for the newest closed candle; no new entries (two-minute limit).",
            )
            if len(lockstep_product_ids(strategy)) > 1:
                await _advance_multi_instrument(
                    snapshot,
                    strategy=strategy,
                    store=store,
                    market_data=market_data,
                    paper_broker=paper_broker,
                    live_broker=live_broker,
                    quote_reader=quote_reader,
                    risk_policy=risk_policy,
                    portfolio=portfolio,
                    primary_product=product,
                    primary_candles=candles,
                    due=(),
                    memory_store=memory_store,
                )
                return
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=product,
            candles=candles,
        )
        return
    covered = lockstep_product_ids(strategy)
    if len(covered) > 1:
        await _advance_multi_instrument(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            risk_policy=risk_policy,
            portfolio=portfolio,
            primary_product=product,
            primary_candles=candles,
            due=due,
            memory_store=memory_store,
        )
        return
    htf_candles = await _closed_htf_window(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    if htf_candles is None:
        detail = "HTF market-data window is gapped or missing the latest completed HTF bar."
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
        await store.save_deployment(paused)
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=(product.product_id,),
            bar_starts_at=due[-1].starts_at,
            reason=DecisionSkipReason.DATA_GAP,
            detail=detail,
        )
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=product,
            candles=candles,
        )
        return
    await _evaluate_strategy_due_bars(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
        quote_reader=quote_reader,
        risk_policy=risk_policy,
        portfolio=portfolio,
        product=product,
        candles=candles,
        due=due,
        htf_candles=htf_candles,
        memory_store=memory_store,
    )


async def _advance_multi_instrument(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    primary_product: MarketProduct,
    primary_candles: Sequence[Candle],
    due: Sequence[Candle],
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Evaluate covered products in lexicographic order on each shared closed bar."""
    covered = lockstep_product_ids(strategy)
    loaded = await _load_lockstep_product_windows(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        covered=covered,
        primary_product=primary_product,
        primary_candles=primary_candles,
    )
    decision_start = (
        due[-1].starts_at
        if due
        else parse_candle_interval(strategy.timeframe).align_closed_end(utc_now())
        - parse_candle_interval(strategy.timeframe).duration
    )
    if loaded is None:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=covered,
            bar_starts_at=decision_start,
            reason=DecisionSkipReason.DATA_GAP,
            detail="A covered product's market-data window is gapped.",
        )
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=primary_product,
            candles=primary_candles,
        )
        return
    windows = loaded.windows
    if loaded.settling:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=covered,
            bar_starts_at=decision_start,
            reason=DecisionSkipReason.BAR_SETTLING,
            detail="A covered product's newest closed candle is settling; no new entries.",
        )
        await _maintain_multi_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            covered=covered,
            windows=windows,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
        )
        return
    deployment = snapshot.deployment
    overlays = await _load_lockstep_filter_windows(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        covered=covered,
        deploy_anchor=deployment.created_at,
    )
    if overlays is None:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=covered,
            bar_starts_at=due[-1].starts_at,
            reason=DecisionSkipReason.DATA_GAP,
            detail="A covered product's HTF or indicator-timeframe window is gapped.",
        )
        await _maintain_multi_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            covered=covered,
            windows=windows,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
        )
        return
    htf_by_product, extra_by_product = overlays
    reference_candles = await _closed_reference_windows(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    broker: Broker = paper_broker
    fee_profile: FeeProfile | None = None
    if deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=primary_product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot, fee_profile = prepared
        broker = live_broker
    if not due:
        await _maintain_multi_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            covered=covered,
            windows=windows,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
        )
        return
    for index, candle in enumerate(due):
        stopped = await _evaluate_lockstep_bar(
            candle,
            covered=covered,
            windows=windows,
            htf_by_product=htf_by_product,
            extra_by_product=extra_by_product,
            reference_candles=reference_candles,
            deployment_id=deployment.id,
            strategy=strategy,
            store=store,
            market_data=market_data,
            broker=broker,
            quote_reader=quote_reader,
            risk_policy=risk_policy,
            portfolio=portfolio,
            memory_store=memory_store,
            fee_profile=fee_profile,
            allow_new_entries=_latest_due_bar_may_enter(
                candle,
                timeframe=strategy.timeframe,
                is_latest=index == len(due) - 1,
            ),
        )
        if stopped:
            return


@dataclass(frozen=True, slots=True)
class LockstepProductWindows:
    """Covered decision-clock windows and whether any newest candle is settling."""

    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]]
    settling: bool


async def _load_lockstep_product_windows(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    covered: tuple[str, ...],
    primary_product: MarketProduct,
    primary_candles: Sequence[Candle],
) -> LockstepProductWindows | None:
    """Load closed LTF windows for every covered product, or pause on a gap."""
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]] = {
        primary_product.product_id: (primary_product, tuple(primary_candles))
    }
    interval = parse_candle_interval(strategy.timeframe)
    expected = interval.align_closed_end(utc_now()) - interval.duration
    if (
        new_closed_bars(
            primary_candles,
            last_evaluated_bar=snapshot.deployment.last_evaluated_bar,
            expected_last_start=expected,
            bar_duration=interval.duration,
            allow_settling=True,
        )
        is None
    ):
        await _pause_coverage_gap(snapshot, store=store, product_id=primary_product.product_id)
        return None
    settling = newest_bar_settling(
        primary_candles, expected_last_start=expected, bar_duration=interval.duration
    )
    for product_id in covered:
        if product_id in windows:
            continue
        extra_product, extra_candles, extra_expected = await _closed_window_for(
            market_data,
            product_id=product_id,
            timeframe=strategy.timeframe,
            warmup_bars=strategy.data_requirements.warmup_bars,
            deploy_anchor=snapshot.deployment.created_at,
        )
        extra_due = new_closed_bars(
            extra_candles,
            last_evaluated_bar=snapshot.deployment.last_evaluated_bar,
            expected_last_start=extra_expected,
            bar_duration=interval.duration,
            allow_settling=True,
        )
        if extra_due is None:
            await _pause_coverage_gap(snapshot, store=store, product_id=product_id)
            return None
        windows[product_id] = (extra_product, extra_candles)
        settling = settling or newest_bar_settling(
            extra_candles, expected_last_start=extra_expected, bar_duration=interval.duration
        )
    return LockstepProductWindows(windows=windows, settling=settling)


async def _load_lockstep_filter_windows(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    covered: tuple[str, ...],
    deploy_anchor: datetime,
) -> tuple[dict[str, tuple[Candle, ...]], dict[str, dict[str, tuple[Candle, ...]]]] | None:
    """Load last-completed HTF and extra-TF windows, or pause on a gap."""
    htf_by_product: dict[str, tuple[Candle, ...]] = {}
    extra_by_product: dict[str, dict[str, tuple[Candle, ...]]] = {}
    for product_id in covered:
        htf_candles = await _closed_htf_window(
            market_data, strategy, product_id=product_id, deploy_anchor=deploy_anchor
        )
        if htf_candles is None:
            paused = with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.PAUSED,
                mismatch_detail=(
                    "HTF market-data window is gapped or missing the latest completed HTF bar "
                    f"on {product_id}."
                ),
            )
            await store.save_deployment(paused)
            return None
        extra_candles = await _closed_indicator_timeframe_windows(
            market_data,
            strategy,
            htf_candles,
            product_id=product_id,
            deploy_anchor=deploy_anchor,
        )
        if extra_candles is None:
            paused = with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.PAUSED,
                mismatch_detail=(
                    "Indicator-timeframe market-data window is gapped or missing the latest "
                    f"completed bar on {product_id}."
                ),
            )
            await store.save_deployment(paused)
            return None
        htf_by_product[product_id] = htf_candles
        extra_by_product[product_id] = extra_candles
    return htf_by_product, extra_by_product


async def _evaluate_lockstep_bar(
    candle: Candle,
    *,
    covered: tuple[str, ...],
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]],
    htf_by_product: dict[str, tuple[Candle, ...]],
    extra_by_product: dict[str, dict[str, tuple[Candle, ...]]],
    reference_candles: dict[str, tuple[Candle, ...]],
    deployment_id: UUID,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    memory_store: ExperientialMemoryStore | None,
    fee_profile: FeeProfile | None = None,
    allow_new_entries: bool = True,
) -> bool:
    """Evaluate every covered product on one shared closed bar. True if the loop should stop.

    One reference-instrument gate applies to every covered product on the shared bar.
    """
    current = await store.get_deployment(deployment_id)
    if current.deployment.status is DeploymentStatus.STOPPED:
        return True
    gate = _bar_reference_gate(
        strategy, reference_candles, candle, allow_new_entries=allow_new_entries
    )
    if gate is not None:
        allow_new_entries = False
    marks: dict[str, Decimal] = {}
    product_bars: dict[str, tuple[MarketProduct, tuple[Candle, ...], Candle]] = {}
    for product_id in covered:
        product, candles = windows[product_id]
        bar = next((item for item in candles if item.starts_at == candle.starts_at), None)
        if bar is None:
            await _pause_coverage_gap(current, store=store, product_id=product_id)
            return True
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        product_bars[product_id] = (product, window, bar)
        marks[product_id] = bar.close
    peer_marks = await _portfolio_marks(
        market_data,
        portfolio=portfolio,
        fallback_timeframe=strategy.timeframe,
        current_product_id=covered[0],
        current_close=marks[covered[0]],
    )
    marks.update(peer_marks)
    for product_id in covered:
        product, window, bar = product_bars[product_id]
        scoped = InstrumentScopedStore(store, product_id)
        focused = await scoped.get_deployment(deployment_id)
        live_base_available = None
        if focused.deployment.mode is DeploymentMode.LIVE and quote_reader is not None:
            live_base_available = await _currency_available(
                quote_reader, base_currency(product.product_id)
            )
        with trade_reason_scope(
            strategy_trade_reason_scope(
                memory_store,
                deployment=focused.deployment,
                strategy=strategy,
                policy=risk_policy,
            )
        ):
            await _journaled_bar(
                focused,
                strategy=strategy,
                product_id=product_id,
                candle=bar,
                allow_new_entries=allow_new_entries,
                reference_gate=gate,
                advance=partial(
                    process_closed_bar,
                    focused,
                    strategy=strategy,
                    product=product,
                    candles=window,
                    broker=broker,
                    store=scoped,
                    risk_policy=risk_policy,
                    portfolio=portfolio,
                    htf_candles=htf_by_product[product_id],
                    indicator_timeframe_candles=extra_by_product[product_id],
                    reference_candles=reference_candles,
                    live_base_available=live_base_available,
                    marks=marks,
                    fee_profile=fee_profile,
                    allow_new_entries=allow_new_entries,
                ),
            )
        latest = await store.get_deployment(deployment_id)
        if latest.deployment.status is DeploymentStatus.STOPPED:
            return True
    parent = await store.get_deployment(deployment_id)
    await store.save_deployment(
        with_runtime(
            parent.deployment,
            updated_at=utc_now(),
            last_evaluated_bar=candle.starts_at,
        )
    )
    return False


async def _pause_coverage_gap(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, product_id: str
) -> None:
    """Pause when any covered product is missing the shared closed bar."""
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail=(
            f"Market-data window is gapped or missing the latest closed bar on {product_id}."
        ),
    )
    await store.save_deployment(paused)


async def _evaluate_strategy_due_bars(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    product: MarketProduct,
    candles: Sequence[Candle],
    due: Sequence[Candle],
    htf_candles: Sequence[Candle],
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Compose extra-TF windows with the shipped closed-bar HTF evaluation path."""
    deployment = snapshot.deployment
    extra_candles = await _indicator_timeframe_windows_or_pause(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        htf_candles=htf_candles,
        deploy_anchor=deployment.created_at,
    )
    if extra_candles is None:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=(product.product_id,),
            bar_starts_at=due[-1].starts_at,
            reason=DecisionSkipReason.DATA_GAP,
            detail="Indicator-timeframe market-data window is gapped.",
        )
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=product,
            candles=candles,
        )
        return
    reference_candles = await _closed_reference_windows(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    broker: Broker = paper_broker
    fee_profile: FeeProfile | None = None
    if deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot, fee_profile = prepared
        broker = live_broker
    last_index = len(due) - 1
    for index, candle in enumerate(due):
        current = await store.get_deployment(deployment.id)
        if current.deployment.status is DeploymentStatus.STOPPED:
            return
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        live_base_available = None
        if current.deployment.mode is DeploymentMode.LIVE and quote_reader is not None:
            live_base_available = await _currency_available(
                quote_reader, base_currency(product.product_id)
            )
        marks = await _portfolio_marks(
            market_data,
            portfolio=portfolio,
            fallback_timeframe=strategy.timeframe,
            current_product_id=product.product_id,
            current_close=candle.close,
        )
        allow_new_entries = _latest_due_bar_may_enter(
            candle,
            timeframe=strategy.timeframe,
            is_latest=index == last_index,
        )
        gate = _bar_reference_gate(
            strategy, reference_candles, candle, allow_new_entries=allow_new_entries
        )
        if gate is not None:
            allow_new_entries = False
        with trade_reason_scope(
            strategy_trade_reason_scope(
                memory_store,
                deployment=current.deployment,
                strategy=strategy,
                policy=risk_policy,
            )
        ):
            await _journaled_bar(
                current,
                strategy=strategy,
                product_id=product.product_id,
                candle=candle,
                allow_new_entries=allow_new_entries,
                reference_gate=gate,
                advance=partial(
                    process_closed_bar,
                    current,
                    strategy=strategy,
                    product=product,
                    candles=window,
                    broker=broker,
                    store=store,
                    risk_policy=risk_policy,
                    portfolio=portfolio,
                    htf_candles=htf_candles,
                    indicator_timeframe_candles=extra_candles,
                    reference_candles=reference_candles,
                    live_base_available=live_base_available,
                    marks=marks,
                    fee_profile=fee_profile,
                    allow_new_entries=allow_new_entries,
                ),
            )


async def _journaled_bar(
    before: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product_id: str,
    candle: Candle,
    allow_new_entries: bool,
    advance: Callable[[], Awaitable[DeploymentSnapshot]],
    require_activity: bool = False,
    reference_gate: ReferenceGate | None = None,
) -> DeploymentSnapshot:
    """Run one closed-bar call and journal what it decided (ADR 0087).

    The call runs exactly as without a journal. A bar that was already evaluated
    (between-bar protection) is not journaled again. ``require_activity`` (flatten
    passes, priced on the latest closed bar) journals only when the call created
    intents or fills, even on an already evaluated bar. A raised call is journaled
    as an error and re-raised unchanged. ``reference_gate`` records why a stale or
    missing reference instrument blocked entries on this bar (ADR 0096).
    """
    if not require_activity and before.deployment.last_evaluated_bar == candle.starts_at:
        return await advance()
    with observe_bar() as observations:
        if reference_gate is not None:
            note_reference_gate(reference_gate)
        try:
            after = await advance()
        except Exception as error:
            await record_bar_decision(
                strategy=strategy,
                product_id=product_id,
                candle=candle,
                before=before,
                after=None,
                observations=observations,
                allow_new_entries=allow_new_entries,
                error=(
                    f"closed-bar processing raised {type(error).__name__}; "
                    "the cycle retries next interval"
                ),
            )
            raise
    if require_activity and not _bar_had_activity(before, after):
        return after
    await record_bar_decision(
        strategy=strategy,
        product_id=product_id,
        candle=candle,
        before=before,
        after=after,
        observations=observations,
        allow_new_entries=allow_new_entries,
    )
    return after


def _bar_had_activity(before: DeploymentSnapshot, after: DeploymentSnapshot) -> bool:
    """Whether a closed-bar call created intents or recorded fills."""
    return len(after.intents) != len(before.intents) or len(after.fills) != len(before.fills)


async def _strategy_definition(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
) -> StrategyDefinition | None:
    """Load the published strategy, or pause when identity is missing."""
    fingerprint = snapshot.deployment.strategy_fingerprint
    if fingerprint is None:
        paused = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Strategy deployment is missing published identity.",
        )
        await store.save_deployment(paused)
        return None
    published = await publication_store.load(fingerprint)
    return published.definition


async def _discretionary_execution_broker(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    store: ExecutionStore,
) -> tuple[DeploymentSnapshot, Broker] | None:
    """Bind the paper or live broker for due discretionary bars.

    Returns None when live prepare fails or a paused mismatch still needs
    protection without evaluating new signals.
    """
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        return snapshot, paper_broker
    prepared = await _prepare_live(
        snapshot,
        store=store,
        live_broker=live_broker,
        quote_reader=quote_reader,
        quote_currency=product.quote_currency,
        product_id=product.product_id,
        cooldown_bars=0,
    )
    if prepared is None or live_broker is None:
        return None
    snapshot, _fee_profile = prepared
    paused_with_mismatch = (
        snapshot.deployment.status is DeploymentStatus.PAUSED
        and snapshot.deployment.mismatch_detail
    )
    if paused_with_mismatch:
        await _maintain_discretionary(
            snapshot,
            product=product,
            candles=candles,
            broker=live_broker,
            store=store,
        )
        return None
    return snapshot, live_broker


async def _process_discretionary(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    user_feed_store: UserOrderFeedStateStore | None,
    risk_policy: RiskPolicyDefinition,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Reconcile and protect a discretionary book without strategy signal evaluation."""
    deployment = snapshot.deployment
    timeframe = deployment.timeframe or "1h"
    try:
        product, candles, expected_last = await _closed_window_for(
            market_data,
            product_id=deployment.product_id,
            timeframe=timeframe,
            warmup_bars=3,
            deploy_anchor=deployment.created_at,
        )
    except WindowCacheWarmingError:
        await _supervise_warming_window(
            snapshot,
            strategy=None,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
        )
        return
    feed_paused = await _pause_five_minute_live_if_feed_down(
        snapshot, timeframe=timeframe, store=store, user_feed_store=user_feed_store
    )
    broker = _cycle_broker(deployment, paper_broker=paper_broker, live_broker=live_broker)
    if not candles:
        await _hold_discretionary_without_candles(
            snapshot, store=store, live_broker=live_broker, feed_paused=feed_paused
        )
        await _maintain_verified_books(
            await store.get_deployment(snapshot.deployment.id),
            strategy=None,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
        )
        return
    if feed_paused:
        await _maintain_discretionary(
            snapshot,
            product=product,
            candles=candles,
            broker=broker,
            store=store,
        )
        return
    interval = parse_candle_interval(timeframe)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
        allow_settling=True,
    )
    if due is None:
        await _pause_gapped_discretionary(
            snapshot,
            store=store,
            product=product,
            candles=candles,
            broker=broker,
        )
        return
    if not due:
        await _maintain_discretionary(
            snapshot,
            product=product,
            candles=candles,
            broker=broker,
            store=store,
        )
        return
    bound = await _discretionary_execution_broker(
        snapshot,
        product=product,
        candles=candles,
        paper_broker=paper_broker,
        live_broker=live_broker,
        quote_reader=quote_reader,
        store=store,
    )
    if bound is None:
        return
    snapshot, broker = bound
    for candle in due:
        current = await store.get_deployment(deployment.id)
        if current.deployment.status is DeploymentStatus.STOPPED:
            return
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        with trade_reason_scope(
            discretionary_trade_reason_scope(
                memory_store,
                policy=risk_policy,
                timeframe=timeframe,
                note=None,
                note_origin=None,
            )
        ):
            await process_discretionary_bar(
                current,
                product=product,
                candles=window,
                broker=broker,
                store=store,
            )


async def _maintain_between_bars(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    product: MarketProduct,
    candles: Sequence[Candle],
) -> None:
    """Reconcile and ensure protection when no newly closed bar is due."""
    del market_data
    snapshot = await store.get_deployment(snapshot.deployment.id)
    broker: Broker = paper_broker
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot, _fee_profile = prepared
        broker = live_broker
    if not candles:
        return
    await _journaled_bar(
        snapshot,
        strategy=strategy,
        product_id=product.product_id,
        candle=candles[-1],
        allow_new_entries=False,
        advance=partial(
            maintain_open_inventory,
            snapshot,
            strategy=strategy,
            product=product,
            candles=candles,
            broker=broker,
            store=store,
        ),
    )


async def _maintain_multi_between_bars(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    covered: tuple[str, ...],
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]],
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
) -> None:
    """Reconcile and ensure protection for every covered product between bars."""
    snapshot = await store.get_deployment(snapshot.deployment.id)
    broker: Broker = paper_broker
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=snapshot.deployment.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot, _fee_profile = prepared
        broker = live_broker
    for product_id in covered:
        product, candles = windows[product_id]
        if not candles:
            continue
        scoped = InstrumentScopedStore(store, product_id)
        focused = await scoped.get_deployment(snapshot.deployment.id)
        await _journaled_bar(
            focused,
            strategy=strategy,
            product_id=product_id,
            candle=candles[-1],
            allow_new_entries=False,
            advance=partial(
                maintain_open_inventory,
                focused,
                strategy=strategy,
                product=product,
                candles=candles,
                broker=broker,
                store=scoped,
            ),
        )


async def _prepare_live(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    quote_currency: str,
    product_id: str,
    cooldown_bars: int,
) -> tuple[DeploymentSnapshot, FeeProfile | None] | None:
    """Pause without a live broker, else reconcile fills and refresh quote cash and fees."""
    deployment = snapshot.deployment
    if live_broker is None:
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Live broker is unavailable.",
        )
        await store.save_deployment(paused)
        return None
    snapshot = await reconcile_open_orders(
        snapshot,
        broker=live_broker,
        store=store,
        product_id=product_id,
        cooldown_bars=cooldown_bars,
    )
    if snapshot.deployment.status is DeploymentStatus.PAUSED:
        return snapshot, None
    if quote_reader is not None:
        available = await _currency_available(quote_reader, quote_currency)
        current = await store.get_deployment(deployment.id)
        stamped = apply_venue_quote(current.deployment, available=available, now=utc_now())
        await store.save_deployment(stamped)
        snapshot = await store.get_deployment(deployment.id)
    fee_profile = await _live_fee_profile(quote_reader)
    return snapshot, fee_profile


async def _live_fee_profile(
    quote_reader: QuoteBalanceReader | None,
) -> FeeProfile | None:
    """Return the venue maker tier when the quote reader exposes fee evidence."""
    if quote_reader is None:
        return None
    get_profile = getattr(quote_reader, "get_fee_profile", None)
    if get_profile is None:
        return None
    try:
        return await get_profile()
    except RuntimeError, ValueError, TypeError, OSError:
        _logger.exception("live_fee_profile_fetch_failed")
        return None


def new_closed_bars(
    candles: Sequence[Candle],
    *,
    last_evaluated_bar: datetime | None,
    expected_last_start: datetime,
    bar_duration: timedelta,
    allow_settling: bool = False,
    now: datetime | None = None,
) -> tuple[Candle, ...] | None:
    """Return due bars, an empty bounded publication wait, or None for unsafe gaps.

    ``allow_settling`` is for decision clocks only. The wait never hides an older
    cursor gap or advances evaluation; consumers must maintain inventory without entries.
    """
    if not candles or not _contiguous(candles, bar_duration):
        return None
    latest = candles[-1]
    if latest.starts_at != expected_last_start:
        if (
            allow_settling
            and newest_bar_settling(
                candles, expected_last_start=expected_last_start, bar_duration=bar_duration, now=now
            )
            and new_closed_bars(
                candles,
                last_evaluated_bar=last_evaluated_bar,
                expected_last_start=expected_last_start - bar_duration,
                bar_duration=bar_duration,
            )
            is not None
        ):
            return ()
        return None
    if last_evaluated_bar is None:
        return (latest,)
    due = tuple(candle for candle in candles if candle.starts_at > last_evaluated_bar)
    expected = last_evaluated_bar + bar_duration
    for candle in due:
        if candle.starts_at != expected:
            return None
        expected = candle.starts_at + bar_duration
    return due


def _contiguous(candles: Sequence[Candle], bar_duration: timedelta) -> bool:
    """Return whether candle starts are consecutive closed bars of one interval."""
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.starts_at - previous != bar_duration:
            return False
        previous = candle.starts_at
    return True


def htf_coverage_ready(
    candles: Sequence[Candle],
    *,
    expected_last_start: datetime,
    bar_duration: timedelta,
) -> bool:
    """True when HTF bars are contiguous and include the latest completed HTF bar."""
    if not candles or not _contiguous(candles, bar_duration):
        return False
    return candles[-1].starts_at == expected_last_start


def _shared_clock_union_warmup(
    strategy: StrategyDefinition, *, timeframe: str, warmup_bars: int, deploy_anchor: datetime
) -> int:
    """Cover the filter clock and every extra-timeframe indicator sharing it (ADR 0113).

    The shared window is fetched once at the union length. Each consumer's indicator rows
    are still selected from its own exact required bars, so the longer window does not move
    a seeded indicator value.
    """
    needed = _required_clock_warmup_bars(
        strategy, timeframe=timeframe, warmup_bars=warmup_bars, deploy_anchor=deploy_anchor
    )
    for clock, indicators in extra_indicator_timeframe_groups(strategy):
        if clock != timeframe:
            continue
        needed = max(
            needed,
            _required_clock_warmup_bars(
                strategy,
                timeframe=timeframe,
                warmup_bars=extra_indicator_timeframe_warmup(
                    indicators, operands=strategy_indicator_operands(strategy)
                ),
                deploy_anchor=deploy_anchor,
            ),
        )
    return needed


async def _closed_htf_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    product_id: str | None = None,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[Candle, ...] | None:
    """Fetch complete-only last-completed HTF bars, or None when gapped.

    The fetch covers the union of the HTF filter warmup and the warmup of every
    extra-timeframe indicator on the same clock, so reusing this window for that clock
    never shortens an indicator's history (ADR 0113).
    """
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        return ()
    _product, candles, expected_last = await _closed_window_for(
        market_data,
        product_id=product_id or strategy.instrument.product_id,
        timeframe=htf_filter.timeframe,
        warmup_bars=_shared_clock_union_warmup(
            strategy,
            timeframe=htf_filter.timeframe,
            warmup_bars=htf_filter.data_requirements.warmup_bars,
            deploy_anchor=deploy_anchor,
        ),
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )
    interval = parse_candle_interval(htf_filter.timeframe)
    if not htf_coverage_ready(
        candles,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
    ):
        return None
    return candles


async def _indicator_timeframe_windows_or_pause(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    htf_candles: Sequence[Candle],
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> dict[str, tuple[Candle, ...]] | None:
    """Return extra-TF windows, or pause when that complete-only coverage is missing."""
    extra_candles = await _closed_indicator_timeframe_windows(
        market_data,
        strategy,
        htf_candles,
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )
    if extra_candles is not None:
        return extra_candles
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail=(
            "Indicator-timeframe market-data window is gapped or missing the latest completed bar."
        ),
    )
    await store.save_deployment(paused)
    return None


async def _closed_indicator_timeframe_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    htf_candles: Sequence[Candle],
    *,
    product_id: str | None = None,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> dict[str, tuple[Candle, ...]] | None:
    """Fetch complete-only extra-TF bars; reuse a shared HTF clock only with union coverage."""
    windows: dict[str, tuple[Candle, ...]] = {}
    htf_timeframe = strategy.htf_filter.timeframe if strategy.htf_filter is not None else None
    covered_product = product_id or strategy.instrument.product_id
    for timeframe, indicators in extra_indicator_timeframe_groups(strategy):
        warmup = _required_clock_warmup_bars(
            strategy,
            timeframe=timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                indicators, operands=strategy_indicator_operands(strategy)
            ),
            deploy_anchor=deploy_anchor,
        )
        if timeframe == htf_timeframe:
            interval = parse_candle_interval(timeframe)
            expected = as_of_closed_start or (
                interval.align_closed_end(datetime.now(UTC)) - interval.duration
            )
            needed_start = warmup_starts_at(
                entry_bar_bucket(deploy_anchor, timeframe), warmup, timeframe
            )
            if (
                htf_candles
                and htf_candles[0].starts_at <= needed_start
                and htf_coverage_ready(
                    htf_candles, expected_last_start=expected, bar_duration=interval.duration
                )
            ):
                windows[timeframe] = tuple(htf_candles)
                continue
        _product, candles, expected_last = await _closed_window_for(
            market_data,
            product_id=covered_product,
            timeframe=timeframe,
            warmup_bars=warmup,
            deploy_anchor=deploy_anchor,
            as_of_closed_start=as_of_closed_start,
        )
        interval = parse_candle_interval(timeframe)
        if not htf_coverage_ready(
            candles,
            expected_last_start=expected_last,
            bar_duration=interval.duration,
        ):
            return None
        windows[timeframe] = candles
    return windows


def _required_clock_warmup_bars(
    strategy: StrategyDefinition,
    *,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
) -> int:
    """Cover the first decision's previous mapped bar without moving the deploy anchor.

    The first evaluated decision is the bar completed at deployment's decision-clock
    bucket. At a required-clock rollover, its previous close maps one bar earlier than
    the deployment bucket's normal warmup. Use the evaluator's coverage contract to
    extend that fixed window only when necessary; later cycles retain the same start.
    """
    decision_interval = parse_candle_interval(strategy.timeframe)
    first_decision = (
        entry_bar_bucket(deploy_anchor, strategy.timeframe) - decision_interval.duration
    )
    required_start, _required_end = closed_bar_required_coverage(
        evaluation_starts_at=first_decision,
        evaluation_ends_at=ltf_close(first_decision, strategy.timeframe),
        timeframe=timeframe,
        warmup_bars=warmup_bars,
    )
    interval = parse_candle_interval(timeframe)
    deploy_bucket = entry_bar_bucket(deploy_anchor, timeframe)
    return max(warmup_bars, (deploy_bucket - required_start) // interval.duration)


async def _closed_reference_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
) -> dict[str, tuple[Candle, ...]]:
    """Fetch each reference instrument's deploy-anchored closed bars (ADR 0096).

    Each window starts at the shared required-clock coverage boundary, so the first
    decision bar's current and previous mapped reference bars are both present at a
    clock rollover and no extra bar is fetched when the anchor sits mid-bucket
    (ADR 0113). Best effort: a reference whose fetch fails is returned empty, so the
    per-bar reference gate skips new entries with ``REFERENCE_DATA_MISSING`` while
    stops, targets, and exits keep running. Strategies without references fetch nothing.
    """
    windows: dict[str, tuple[Candle, ...]] = {}
    for requirement in reference_data_requirements(strategy):
        try:
            _product, candles, _expected = await _closed_window_for(
                market_data,
                product_id=requirement.product_id,
                timeframe=requirement.timeframe,
                warmup_bars=_required_clock_warmup_bars(
                    strategy,
                    timeframe=requirement.timeframe,
                    warmup_bars=requirement.warmup_bars,
                    deploy_anchor=deploy_anchor,
                ),
                deploy_anchor=deploy_anchor,
            )
        except WindowCacheWarmingError:
            # References gate only entries; warming must not suppress protective supervision.
            candles = ()
        except RuntimeError, ValueError, TypeError, OSError:
            _logger.warning(
                "reference_window_unavailable reference_id=%s product_id=%s timeframe=%s",
                requirement.reference_id,
                requirement.product_id,
                requirement.timeframe,
            )
            candles = ()
        windows[requirement.reference_id] = candles
    return windows


def _bar_reference_gate(
    strategy: StrategyDefinition,
    reference_candles: dict[str, tuple[Candle, ...]],
    candle: Candle,
    *,
    allow_new_entries: bool,
) -> ReferenceGate | None:
    """Gate one entry-eligible decision bar on reference readiness; None when it may enter."""
    if not allow_new_entries or not reference_data_requirements(strategy):
        return None
    return reference_gate(
        strategy,
        reference_candles,
        decision_close=ltf_close(candle.starts_at, strategy.timeframe),
    )


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch deploy-anchored warmup through the latest fully closed bar."""
    return await _closed_window_for(
        market_data,
        product_id=strategy.instrument.product_id,
        timeframe=strategy.timeframe,
        warmup_bars=strategy.data_requirements.warmup_bars,
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )


async def _closed_window_for(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch deploy-anchored warmup through one closed bar on an interval.

    The service's deploy-window cache (ADR 0113) segments settled history below the
    adapter's per-request bound and refetches the newest/unsettled tail with overlap.
    The start never slides: seeded indicators still consume their exact canonical
    deploy-prefix. Frozen history ignores provider corrections within this service
    generation; restart/eviction re-observes history from the same boundary. Budget
    exhaustion raises WindowCacheWarmingError, not an empty/gapped data verdict:
    consumers retry without changing lifecycle choices or evaluating a partial seed.
    Retained history and full indicator compute still grow with deployment lifetime.

    Coinbase returns no candle for an interval without trades. A bar missing between two
    real candles, and still missing on one re-fetch, is a confirmed no-trade interval and
    becomes a flat zero-volume bar, exactly as research datasets publish it (ADR 0095).
    A missing newest bar is never filled; decision clocks may wait for ADR 0104's
    bounded publication window before pausing. Required filter clocks remain fail-closed.
    """
    now = datetime.now(UTC)
    interval = parse_candle_interval(timeframe)
    last_closed_end = interval.align_closed_end(now)
    last_closed_start = (
        as_of_closed_start
        if as_of_closed_start is not None
        else last_closed_end - interval.duration
    )
    deploy_anchor_bar = entry_bar_bucket(deploy_anchor, timeframe)
    starts_at = warmup_starts_at(deploy_anchor_bar, warmup_bars, timeframe)
    preview = await market_data.get_preview(product_id, interval)

    async def fetch_range(starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Fetch one bounded segment at this cycle's immutable observation instant."""
        return await market_data.get_range(product_id, interval, starts_at, ends_at, now)

    candles = await market_data.window_cache.closed_window(
        fetch_range,
        product_id=product_id,
        interval=interval,
        starts_at=starts_at,
        last_closed_start=last_closed_start,
        now=now,
    )
    return preview.product, candles, last_closed_start


async def _currency_available(reader: QuoteBalanceReader, currency: str) -> Decimal | None:
    """Return available units of one venue currency, if reported."""
    balances = await reader.list_balances()
    for balance in balances:
        if balance.currency == currency:
            return balance.available
    return None


async def _portfolio_marks(
    market_data: MarketDataService,
    *,
    portfolio: Sequence[DeploymentSnapshot],
    fallback_timeframe: str,
    current_product_id: str,
    current_close: Decimal,
) -> dict[str, Decimal]:
    """Last-close marks for occupied products so mode-wide daily-loss can fail closed."""
    marks: dict[str, Decimal] = {current_product_id: current_close}
    for snapshot in portfolio:
        timeframe = snapshot.deployment.timeframe or fallback_timeframe
        product_ids = {snapshot.deployment.product_id}
        for runtime in snapshot.instrument_runtimes:
            product_ids.add(runtime.product_id)
        for position in snapshot.positions:
            if position.product_id:
                product_ids.add(position.product_id)
        for product_id in product_ids:
            if product_id in marks:
                continue
            close = await _last_close(market_data, product_id=product_id, timeframe=timeframe)
            if close is not None:
                marks[product_id] = close
    return marks


async def _last_close(
    market_data: MarketDataService, *, product_id: str, timeframe: str
) -> Decimal | None:
    """Return the latest complete close, or None when that window is empty."""
    preview = await market_data.get_preview(product_id, parse_candle_interval(timeframe))
    candles = preview.quality.candles
    if not candles:
        return None
    return candles[-1].close


async def _risk_snapshots(
    store: ExecutionStore,
    deployments: Sequence[Deployment],
) -> tuple[DeploymentSnapshot, ...]:
    """Load snapshots used by the entry gate, including stopped residual books."""
    loaded = [await store.get_deployment(item.id) for item in deployments]
    paper = risk_bearing_snapshots(loaded, DeploymentMode.PAPER)
    live = risk_bearing_snapshots(loaded, DeploymentMode.LIVE)
    return paper + live


def _cycle_broker(
    deployment: Deployment,
    *,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> Broker:
    """Select the live broker when the book is live and a live adapter is bound."""
    if deployment.mode is DeploymentMode.LIVE and live_broker is not None:
        return live_broker
    return paper_broker


async def _pause_gapped_discretionary(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
) -> None:
    """Pause a discretionary book on a gapped window, then keep residual protection."""
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail="Market-data window is gapped or missing the latest closed bar.",
    )
    await store.save_deployment(paused)
    await _maintain_discretionary(
        snapshot,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
    )


async def _maintain_discretionary(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> None:
    """Reconcile owned orders and keep protection when no new discretionary bar is due."""
    snapshot = await store.get_deployment(snapshot.deployment.id)
    current = await reconcile_open_orders(
        snapshot,
        broker=broker,
        store=store,
        product_id=product.product_id,
        cooldown_bars=0,
    )
    if not candles:
        return
    await process_discretionary_bar(
        current,
        product=product,
        candles=tuple(candles),
        broker=broker,
        store=store,
    )


USER_FEED_PAUSE_DETAIL = "User-order feed is not connected."


async def _pause_five_minute_live_if_feed_down(
    snapshot: DeploymentSnapshot,
    *,
    timeframe: str,
    store: ExecutionStore,
    user_feed_store: UserOrderFeedStateStore | None,
) -> bool:
    """Pause sub-hour live entries when the user-order feed is down; clear that pause on recovery.

    True means new entries are disabled for this cycle. Callers still reconcile
    and maintain verified protection on owned orders.

    Only a RUNNING book is paused for the feed, so the feed reason never
    overwrites an operator pause or another fail-closed mismatch. When the feed
    is connected and fresh again, a book whose *sole* pause reason is the feed
    (worker pause, no lifecycle command, no breaker latch) resumes with an audit
    row. Every other pause or latch stays until the operator acts.
    """
    deployment = snapshot.deployment
    if deployment.mode is not DeploymentMode.LIVE:
        return False
    interval = parse_candle_interval(timeframe)
    if not interval.requires_live_user_feed:
        return False
    if await _user_feed_connected(user_feed_store):
        await _clear_feed_only_pause(deployment, store=store)
        return False
    if deployment.status is not DeploymentStatus.RUNNING:
        return True
    paused = with_runtime(
        deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail=USER_FEED_PAUSE_DETAIL,
    )
    await store.save_deployment(paused)
    await record_execution_audit(
        action="user_feed_pause",
        outcome=AuditEventOutcome.INFO,
        detail=(
            f"deployment_id={deployment.id} timeframe={timeframe}: sub-hour live paused "
            "because the user-order feed is not connected and fresh."
        ),
        product_id=deployment.product_id,
    )
    return True


def _is_feed_only_pause(deployment: Deployment) -> bool:
    """True when the user-feed gate is the only reason this live book is paused."""
    return (
        deployment.status is DeploymentStatus.PAUSED
        and deployment.mismatch_detail == USER_FEED_PAUSE_DETAIL
        and deployment.lifecycle_command is LifecycleCommand.NONE
        and not deployment.daily_loss_latched
        and not deployment.drawdown_latched
    )


async def _clear_feed_only_pause(deployment: Deployment, *, store: ExecutionStore) -> None:
    """Resume a book paused solely by the user-feed gate once the feed is healthy."""
    if not _is_feed_only_pause(deployment):
        return
    resumed = with_runtime(
        deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.RUNNING,
        clear_mismatch=True,
    )
    await store.save_deployment(resumed)
    await record_execution_audit(
        action="user_feed_pause_cleared",
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"deployment_id={deployment.id}: user-order feed is connected and fresh again; "
            "the feed-only pause was cleared automatically. Entries resume on the next due bar."
        ),
        product_id=deployment.product_id,
    )


async def _user_feed_connected(store: UserOrderFeedStateStore | None) -> bool:
    """True only when the durable user-order feed snapshot is connected and fresh."""
    if store is None:
        return False
    try:
        snapshot = await store.get()
    except UserOrderFeedUnavailableError:
        return False
    if snapshot is None or snapshot.state is not UserOrderFeedState.CONNECTED:
        return False
    heartbeat_at = snapshot.last_heartbeat_at
    if heartbeat_at is None:
        return False
    age = (datetime.now(UTC) - heartbeat_at).total_seconds()
    return age < DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
