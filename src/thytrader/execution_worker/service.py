"""Continuously evaluate deployed strategies against closed candles."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.exchanges.ws.market_feed import DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
from thytrader.execution.ids import utc_now
from thytrader.execution.loop import cancel_resting_orders, process_closed_bar
from thytrader.execution.models import DeploymentMode, DeploymentStatus, with_runtime
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.user_feed_state import UserOrderFeedState, UserOrderFeedUnavailableError
from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.store import load_effective_policy

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal
    from uuid import UUID

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.execution.broker import Broker
    from thytrader.execution.models import Deployment, DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.publication import StrategyPublicationStore

_logger = logging.getLogger(__name__)


class QuoteBalanceReader(Protocol):
    """Read quote cash for live sizing."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return non-empty exchange balances."""
        ...


async def run_execution_worker(
    stop_requested: asyncio.Event,
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
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
) -> None:
    """Poll running deployments until shutdown."""
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    try:
        while not stop_requested.is_set():
            if heartbeat_store is not None:
                await heartbeat_store.touch("execution_worker", datetime.now(UTC))
            await _run_cycle(
                store=store,
                publication_store=publication_store,
                market_data=market_data,
                paper_broker=paper_broker,
                live_broker=live_broker,
                quote_reader=quote_reader,
                risk_store=risk_store,
                user_feed_store=user_feed_store,
            )
            if wake_requested is not None:
                wake_requested.clear()
            await _await_next_cycle(
                stop_requested, wake_requested=wake_requested, interval_seconds=interval_seconds
            )
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


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
    publication_store: StrategyPublicationStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_store: RiskPolicyStore | None,
    user_feed_store: UserOrderFeedStateStore | None = None,
) -> None:
    """Process occupied deployments once, refreshing occupancy after each for the entry gate."""
    policy = (await load_effective_policy(risk_store)).definition
    deployments = await store.list_deployments()
    portfolio = await _occupied_snapshots(store, deployments)
    for deployment in deployments:
        if deployment.status is DeploymentStatus.STOPPED:
            try:
                await _cancel_stopped(
                    deployment_id=deployment.id,
                    store=store,
                    paper_broker=paper_broker,
                    live_broker=live_broker,
                )
            except RuntimeError, ValueError, TypeError, OSError:
                _logger.exception("execution_cancel_failed deployment_id=%s", deployment.id)
            continue
        if deployment.status not in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}:
            continue
        try:
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
            )
        except RuntimeError, ValueError, TypeError, OSError:
            _logger.exception("execution_cycle_failed deployment_id=%s", deployment.id)
        portfolio = await _occupied_snapshots(store, deployments)


async def _cancel_stopped(
    *,
    deployment_id: UUID,
    store: ExecutionStore,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> None:
    """Cancel resting orders on a permanently stopped deployment."""
    snapshot = await store.get_deployment(deployment_id)
    broker = paper_broker
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        if live_broker is None:
            return
        broker = live_broker
    await cancel_resting_orders(snapshot, broker=broker, store=store)


async def _process_one(
    *,
    deployment_id: UUID,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    user_feed_store: UserOrderFeedStateStore | None,
) -> None:
    """Load evidence and advance one deployment through newly closed bars."""
    snapshot = await store.get_deployment(deployment_id)
    deployment = snapshot.deployment
    published = await publication_store.load(deployment.strategy_fingerprint)
    strategy = published.definition
    if await _pause_five_minute_live_if_feed_down(
        snapshot, strategy=strategy, store=store, user_feed_store=user_feed_store
    ):
        return
    product, candles, expected_last = await _closed_window(market_data, strategy)
    if not candles:
        return
    interval = parse_candle_interval(strategy.timeframe)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
    )
    if due is None:
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Market-data window is gapped or missing the latest closed bar.",
        )
        await store.save_deployment(paused)
        return
    broker: Broker = paper_broker
    if deployment.mode is DeploymentMode.LIVE:
        snapshot = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if snapshot is None or live_broker is None:
            return
        broker = live_broker
        paused_with_mismatch = (
            snapshot.deployment.status is DeploymentStatus.PAUSED
            and snapshot.deployment.mismatch_detail
        )
        if paused_with_mismatch:
            return
    for candle in due:
        snapshot = await store.get_deployment(deployment_id)
        if snapshot.deployment.status is DeploymentStatus.STOPPED:
            return
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        await process_closed_bar(
            snapshot,
            strategy=strategy,
            product=product,
            candles=window,
            broker=broker,
            store=store,
            risk_policy=risk_policy,
            portfolio=portfolio,
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
) -> DeploymentSnapshot | None:
    """Pause without a live broker, else reconcile fills then refresh quote cash."""
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
        return snapshot
    if quote_reader is not None:
        cash = await _quote_cash(quote_reader, quote_currency)
        if cash is not None:
            current = await store.get_deployment(deployment.id)
            await store.save_deployment(
                with_runtime(current.deployment, updated_at=utc_now(), cash=cash)
            )
            snapshot = await store.get_deployment(deployment.id)
    return snapshot


def new_closed_bars(
    candles: Sequence[Candle],
    *,
    last_evaluated_bar: datetime | None,
    expected_last_start: datetime,
    bar_duration: timedelta,
) -> tuple[Candle, ...] | None:
    """Return newly closed bars in order, or None when the window is gapped or stale."""
    if not candles or not _contiguous(candles, bar_duration):
        return None
    latest = candles[-1]
    if latest.starts_at != expected_last_start:
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


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch warmup plus the latest fully closed bar on the strategy interval."""
    now = datetime.now(UTC)
    interval = parse_candle_interval(strategy.timeframe)
    last_closed_end = interval.align_closed_end(now)
    last_closed_start = last_closed_end - interval.duration
    warmup = strategy.data_requirements.warmup_bars
    starts_at = last_closed_start - interval.duration * warmup
    preview = await market_data.get_preview(strategy.instrument.product_id, interval)
    report = await market_data.get_range(
        strategy.instrument.product_id, interval, starts_at, last_closed_end, now
    )
    candles = tuple(
        candle
        for candle in report.quality.candles
        if starts_at <= candle.starts_at <= last_closed_start
    )
    return preview.product, candles, last_closed_start


async def _quote_cash(reader: QuoteBalanceReader, quote_currency: str) -> Decimal | None:
    """Return available quote cash when the venue reports that currency."""
    balances = await reader.list_balances()
    for balance in balances:
        if balance.currency == quote_currency:
            return balance.available
    return None


async def _occupied_snapshots(
    store: ExecutionStore,
    deployments: Sequence[Deployment],
) -> tuple[DeploymentSnapshot, ...]:
    """Load current snapshots for running and paused deployments used by the entry gate."""
    occupied = [
        await store.get_deployment(item.id)
        for item in deployments
        if item.status in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
    ]
    return tuple(occupied)


async def _pause_five_minute_live_if_feed_down(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    user_feed_store: UserOrderFeedStateStore | None,
) -> bool:
    """Pause 5m live when the user-order feed is down. True means the cycle must stop."""
    deployment = snapshot.deployment
    if deployment.mode is not DeploymentMode.LIVE or strategy.timeframe != "5m":
        return False
    if await _user_feed_connected(user_feed_store):
        return False
    paused = with_runtime(
        deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail="User-order feed is not connected.",
    )
    await store.save_deployment(paused)
    return True


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
