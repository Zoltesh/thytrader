"""Cycle and safety supervision helpers of the execution worker.

Durable safety alerts and failure pauses (ADR 0115), missing/gapped/warming
decision-window pauses with verified-book protection, and the 5m user-feed gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.alerts.models import AlertCheck, AlertCode, SupervisionFinding
from thytrader.alerts.store import AlertStoreError
from thytrader.alerts.supervision import gather_safety_findings
from thytrader.alerts.supervision_rows import failure_error_type, verified_worker_recovery
from thytrader.audit_events import AuditEventOutcome
from thytrader.exchanges.ws.market_feed import DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.decision_journal import record_gate_skip
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.leases import RevisionFencedStore, acquire_worker_lease
from thytrader.execution.live_protection import maintain_discretionary_protection
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.stopped import load_verified_exit_context, stopped_product_ids
from thytrader.execution.user_feed_state import UserOrderFeedState, UserOrderFeedUnavailableError
from thytrader.execution_worker.ports import _logger
from thytrader.execution_worker.windows import _closed_window_for
from thytrader.market_data.models import parse_candle_interval
from thytrader.strategies.models import lockstep_product_ids
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    LifecycleCommand,
    with_runtime,
)
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from thytrader.alerts.models import OperatorAlert
    from thytrader.alerts.service import AlertService
    from thytrader.alerts.store import AlertApplication
    from thytrader.alerts.supervision_inputs import ClosedCandleReader
    from thytrader.execution.broker import Broker
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import Deployment, DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def _supervise_safety(
    *,
    alert_service: AlertService | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    deployments: tuple[Deployment, ...],
    cycle_failures: list[SupervisionFinding],
    worker_interval_seconds: int,
    observed_at: datetime,
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
        # Positive error evidence does not depend on snapshot/inventory completeness.
        if cycle_failures:
            await alert_service.apply(cycle_failures, now=observed_at, dispatch=False)
        previous = await alert_service.open_alerts()
        # The protocol's unbounded list is authoritative only after a successful read.
        current = await store.list_deployments()
        evidence = await gather_safety_findings(
            deployments=current,
            snapshots=store,
            closed_candles=_supervision_candle_reader(market_data),
            # Ordering uses the cycle-start watermark; freshness uses the actual
            # evaluation time, after this cycle's venue reads have completed.
            now=utc_now(),
            thresholds=alert_service.thresholds,
            worker_interval_seconds=worker_interval_seconds,
            prior_alerts=previous,
            inventory_authoritative=True,
        )
        before = {item.id: item for item in deployments}
        failed = {item.subject for item in cycle_failures}
        verified_success = tuple(
            AlertCheck(AlertCode.WORKER_BOOK_FAILURES, str(item.id))
            for item in current
            if str(item.id) not in failed
            and item.id in before
            and verified_worker_recovery(before[item.id], item)
        )
        application = await alert_service.apply(
            (*cycle_failures, *evidence.findings),
            now=observed_at,
            evaluated=(*evidence.evaluated, *verified_success),
            dispatch=False,
        )
        outstanding = await alert_service.open_alerts()
    except AlertStoreError, RuntimeError, ValueError, OSError:
        _logger.warning("safety_supervision_evidence_unavailable")
        return
    await _pause_repeatedly_failing_books(
        store,
        application,
        deployments=tuple(
            item
            for item in current
            if item.id in before
            and item.strategy_fingerprint == before[item.id].strategy_fingerprint
            and item.strategy_id == before[item.id].strategy_id
        ),
        consecutive_failure_cycles=alert_service.thresholds.consecutive_failure_cycles,
        outstanding=outstanding,
    )


def _supervision_candle_reader(market_data: MarketDataService) -> ClosedCandleReader:
    """Adapt the worker's closed-window fetch to best-effort supervision reads."""

    async def read(product_id: str, timeframe: str, deploy_anchor: datetime) -> tuple[Candle, ...]:
        _product, candles, expected = await _closed_window_for(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=3,
            deploy_anchor=deploy_anchor,
        )
        # Empty/settling/late/warming windows are unknown, not verified recovery.
        if not candles or candles[-1].starts_at < expected:
            return ()
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
    outstanding: tuple[OperatorAlert, ...] = (),
) -> None:
    """Fence entry pauses, including a threshold persisted before a worker crash."""
    by_id = {deployment.id: deployment for deployment in deployments}
    alerts = {row.id: row for row in (*outstanding, *(item.alert for item in application.changes))}
    for alert in alerts.values():
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
        error_type = failure_error_type(alert.detail)
        last_error = "" if error_type is None else f" (last error: {error_type})"
        detail = (
            f"WORKER_CONSECUTIVE_FAILURES: entries paused after {alert.occurrences} failed "
            f"supervision cycles{last_error}; exits and reconciliation continue. Operator "
            "review required; resume is manual."
        )
        try:
            leased = await acquire_worker_lease(store, deployment.id)
            if leased is None or leased.revision != deployment.revision + 1:
                continue
            loaded = (await store.get_deployment(deployment.id)).deployment
            if loaded.revision != leased.revision or (
                loaded.strategy_id != deployment.strategy_id
                or loaded.strategy_fingerprint != deployment.strategy_fingerprint
                or not _may_pause_for_failures(
                    loaded, alert.occurrences, consecutive_failure_cycles
                )
            ):
                continue
            fenced = RevisionFencedStore(store, loaded.id, loaded.revision)
            paused = with_runtime(
                loaded,
                updated_at=utc_now(),
                status=DeploymentStatus.PAUSED,
                lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
                mismatch_detail=detail,
            )
            await fenced.save_deployment(paused)
        except RuntimeError, ValueError, OSError:
            # Deleted books, lease/revision races and storage failure retain error evidence.
            _logger.warning("safety_pause_not_applied deployment_id=%s", deployment.id)
            continue
        await record_execution_audit(
            action="worker_consecutive_failures_pause",
            outcome=AuditEventOutcome.FAILURE,
            detail=(
                f"deployment_id={deployment.id}: paused new entries after "
                f"{alert.occurrences} consecutive failed cycles (ADR 0115)."
            ),
            product_id=deployment.product_id,
        )


_MISSING_DECISION_CANDLES = "Market-data window is gapped or missing the latest closed bar."


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
            strategy=strategy,
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


async def _pause_coverage_gap(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, product_id: str
) -> None:
    """Pause running books on missing coverage without clearing an operator pause."""
    await _pause_running_for_data_gap(
        snapshot,
        store=store,
        detail=f"Market-data window is gapped or missing the latest closed bar on {product_id}.",
    )


async def _pause_running_for_data_gap(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, detail: str
) -> None:
    """Retain paused/stopped lifecycle choices when decision history becomes gapped."""
    current = await store.get_deployment(snapshot.deployment.id)
    if current.deployment.status is not DeploymentStatus.RUNNING:
        return
    await store.save_deployment(
        with_runtime(
            current.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
    )


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
