"""Continuously evaluate deployed strategies against closed 1h candles."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.execution.ids import utc_now
from thytrader.execution.loop import cancel_resting_orders, process_closed_bar
from thytrader.execution.models import DeploymentMode, DeploymentStatus, with_runtime
from thytrader.execution.reconcile import reconcile_open_orders

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal
    from uuid import UUID

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.execution.broker import Broker
    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
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
) -> None:
    """Poll running deployments until shutdown."""
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    try:
        while not stop_requested.is_set():
            await _run_cycle(
                store=store,
                publication_store=publication_store,
                market_data=market_data,
                paper_broker=paper_broker,
                live_broker=live_broker,
                quote_reader=quote_reader,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


async def _run_cycle(
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
) -> None:
    """Process every running or paused deployment once, and cancel stopped restings."""
    deployments = await store.list_deployments()
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
            )
        except RuntimeError, ValueError, TypeError, OSError:
            _logger.exception("execution_cycle_failed deployment_id=%s", deployment.id)


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
) -> None:
    """Load evidence and advance one deployment through newly closed bars."""
    snapshot = await store.get_deployment(deployment_id)
    deployment = snapshot.deployment
    published = await publication_store.load(deployment.strategy_fingerprint)
    strategy = published.definition
    product, candles, expected_last = await _closed_window(market_data, strategy)
    if not candles:
        return
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
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
) -> tuple[Candle, ...] | None:
    """Return newly closed bars in order, or None when the window is gapped or stale."""
    if not candles or not _hourly_contiguous(candles):
        return None
    latest = candles[-1]
    if latest.starts_at != expected_last_start:
        return None
    if last_evaluated_bar is None:
        return (latest,)
    due = tuple(candle for candle in candles if candle.starts_at > last_evaluated_bar)
    expected = last_evaluated_bar + timedelta(hours=1)
    for candle in due:
        if candle.starts_at != expected:
            return None
        expected = candle.starts_at + timedelta(hours=1)
    return due


def _hourly_contiguous(candles: Sequence[Candle]) -> bool:
    """Return whether candle starts are consecutive UTC hours."""
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.starts_at - previous != timedelta(hours=1):
            return False
        previous = candle.starts_at
    return True


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch warmup plus the latest fully closed 1h bar."""
    now = datetime.now(UTC)
    ends_at = now.replace(minute=0, second=0, microsecond=0)
    last_closed_end = ends_at
    last_closed_start = last_closed_end - timedelta(hours=1)
    warmup = strategy.data_requirements.warmup_bars
    starts_at = last_closed_start - timedelta(hours=warmup)
    preview = await market_data.get_hourly_preview(strategy.instrument.product_id)
    report = await market_data.get_hourly_range(
        strategy.instrument.product_id, starts_at, last_closed_end, now
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
