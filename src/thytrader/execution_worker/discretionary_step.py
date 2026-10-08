"""Per-deployment discretionary book processing for one execution-worker cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.discretionary import process_discretionary_bar
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.trade_reason_scope import (
    discretionary_trade_reason_scope,
    trade_reason_scope,
)
from thytrader.execution_worker.live_sizing import _prepare_live
from thytrader.execution_worker.supervision import (
    _cycle_broker,
    _maintain_verified_books,
    _pause_five_minute_live_if_feed_down,
    _pause_running_for_missing_candles,
    _supervise_warming_window,
)
from thytrader.execution_worker.windows import _closed_window_for, new_closed_bars
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.trading.ids import utc_now
from thytrader.trading.models import DeploymentMode, DeploymentStatus, with_runtime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.broker import Broker
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.execution_worker.ports import QuoteBalanceReader
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


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
