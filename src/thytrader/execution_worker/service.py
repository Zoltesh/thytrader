"""Continuously evaluate deployed strategies against closed 1h candles."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.execution.ids import utc_now
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.models import DeploymentMode, DeploymentStatus, with_runtime
from thytrader.execution.reconcile import reconcile_open_orders

if TYPE_CHECKING:
    from collections.abc import Callable
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
    """Process every running deployment once."""
    deployments = await store.list_deployments()
    for deployment in deployments:
        if deployment.status is not DeploymentStatus.RUNNING:
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
    """Load evidence and advance one deployment by at most one closed bar."""
    snapshot = await store.get_deployment(deployment_id)
    deployment = snapshot.deployment
    published = await publication_store.load(deployment.strategy_fingerprint)
    strategy = published.definition
    product, candles = await _closed_window(market_data, strategy)
    if not candles:
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
        )
        if snapshot is None or live_broker is None:
            return
        broker = live_broker
    await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
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
) -> DeploymentSnapshot | None:
    """Pause without a live broker, else refresh quote cash and reconcile fills."""
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
    if quote_reader is not None:
        cash = await _quote_cash(quote_reader, quote_currency)
        if cash is not None:
            current = await store.get_deployment(deployment.id)
            await store.save_deployment(
                with_runtime(current.deployment, updated_at=utc_now(), cash=cash)
            )
            snapshot = await store.get_deployment(deployment.id)
    return await reconcile_open_orders(
        snapshot, broker=live_broker, store=store, product_id=product_id
    )


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
) -> tuple[MarketProduct, tuple[Candle, ...]]:
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
    return preview.product, candles


async def _quote_cash(reader: QuoteBalanceReader, quote_currency: str) -> Decimal | None:
    """Return available quote cash when the venue reports that currency."""
    balances = await reader.list_balances()
    for balance in balances:
        if balance.currency == quote_currency:
            return balance.available
    return None
