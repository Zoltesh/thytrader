"""One closed-candle cycle for a deployed strategy."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError
from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
    with_runtime,
)
from thytrader.execution.signals import evaluate_latest_entry, latest_atr
from thytrader.execution.sizing import size_long_entry
from thytrader.execution.submit import submit_intent
from thytrader.research.trace import EntryConditionOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.execution.broker import Broker
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.strategies.models import StrategyDefinition


async def process_closed_bar(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Advance one running deployment by exactly one newly closed candle."""
    if snapshot.deployment.status is not DeploymentStatus.RUNNING or not candles:
        return snapshot
    candle = candles[-1]
    deployment = snapshot.deployment
    if deployment.last_evaluated_bar == candle.starts_at:
        return await _ensure_take_profit_if_open(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    snapshot = await _match_resting_orders(snapshot, candle=candle, broker=broker, store=store)
    snapshot = await _manage_position(
        snapshot, strategy=strategy, candle=candle, product=product, broker=broker, store=store
    )
    snapshot = await _maybe_enter(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        candle=candle,
        broker=broker,
        store=store,
    )
    cooldown = snapshot.deployment.cooldown_bars_remaining
    if cooldown > 0:
        cooldown -= 1
    updated = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        last_evaluated_bar=candle.starts_at,
        cooldown_bars_remaining=cooldown,
    )
    await store.save_deployment(updated)
    return await store.get_deployment(updated.id)


async def _match_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Apply paper or local fills for resting limits against the closed candle."""
    for order in snapshot.orders:
        if order.status is not OrderStatus.OPEN:
            continue
        fill = broker.match_open_order(order, candle)
        if fill is None:
            continue
        await store.save_fill(fill)
        filled = replace(
            order,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            updated_at=utc_now(),
        )
        await store.save_order(filled)
        snapshot = await apply_fill(snapshot, fill=fill, order=filled, store=store)
    return await store.get_deployment(snapshot.deployment.id)


async def apply_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Update cash and position from one fill and persist the result."""
    if order.side is OrderSide.BUY:
        return await _apply_buy_fill(snapshot, fill=fill, store=store)
    return await _apply_sell_fill(snapshot, fill=fill, store=store)


async def _apply_buy_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Open the long from an entry fill, or pause when stop/target prices are missing."""
    deployment = snapshot.deployment
    now = utc_now()
    cash = deployment.cash - (fill.price * fill.quantity) - fill.fee
    stop = deployment.pending_stop_price
    target = deployment.pending_target_price
    if stop is None or target is None:
        paused = with_runtime(
            deployment,
            updated_at=now,
            cash=cash,
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Entry fill is missing stored stop/target prices.",
            phase=RuntimePhase.FLAT,
            clear_pending_levels=True,
        )
        await store.save_deployment(paused)
        await store.save_position(None, deployment_id=deployment.id)
        return await store.get_deployment(deployment.id)
    position = Position(
        deployment_id=deployment.id,
        quantity=fill.quantity,
        entry_price=fill.price,
        stop_price=stop,
        target_price=target,
        entered_bar=fill.filled_at,
        updated_at=now,
    )
    updated = with_runtime(
        deployment,
        updated_at=now,
        cash=cash,
        phase=RuntimePhase.OPEN,
        bars_held=0,
        pending_entry_bars=0,
        clear_pending_levels=True,
    )
    await store.save_position(position, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


async def _apply_sell_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Flatten the long after an exit fill."""
    deployment = snapshot.deployment
    cash = deployment.cash + (fill.price * fill.quantity) - fill.fee
    updated = with_runtime(
        deployment,
        updated_at=utc_now(),
        cash=cash,
        phase=RuntimePhase.FLAT,
        bars_held=0,
        pending_entry_bars=0,
        cooldown_bars_remaining=0,
        clear_pending_levels=True,
    )
    await store.save_position(None, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


async def _manage_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Exit on stop, take-profit fill wait, or time, and cancel stale entries."""
    deployment = snapshot.deployment
    if deployment.phase is RuntimePhase.PENDING_ENTRY:
        return await _manage_pending_entry(
            snapshot, strategy=strategy, candle=candle, product=product, broker=broker, store=store
        )
    position = snapshot.position
    if deployment.phase is not RuntimePhase.OPEN or position is None:
        return snapshot
    bars_held = deployment.bars_held + 1
    updated = with_runtime(deployment, updated_at=utc_now(), bars_held=bars_held)
    await store.save_deployment(updated)
    snapshot = await store.get_deployment(deployment.id)
    if candle.low <= position.stop_price:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.STOP,
            price=position.stop_price,
        )
    if bars_held >= strategy.exits.time_exit.max_bars_held:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.TIME_EXIT,
            price=candle.close,
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


async def _manage_pending_entry(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Wait, cancel, or reprice an unfilled maker entry."""
    deployment = snapshot.deployment
    waited = deployment.pending_entry_bars + 1
    open_entry = _open_side(snapshot.orders, OrderSide.BUY)
    if open_entry is None:
        return await _flatten_pending(snapshot, store=store, cooldown_bars=1)
    if waited < strategy.execution.max_entry_wait_bars:
        waited_state = with_runtime(deployment, updated_at=utc_now(), pending_entry_bars=waited)
        await store.save_deployment(waited_state)
        return await store.get_deployment(deployment.id)
    snapshot = await _cancel_one_order(open_entry, broker=broker, store=store)
    if strategy.execution.on_unfilled_entry == "reprice":
        return await _reprice_entry(
            snapshot, candle=candle, product=product, broker=broker, store=store, prior=open_entry
        )
    return await _flatten_pending(
        snapshot, store=store, cooldown_bars=max(strategy.entry.cooldown_bars, 1)
    )


async def _flatten_pending(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, cooldown_bars: int
) -> DeploymentSnapshot:
    """Return a flat deployment after an unfilled entry is abandoned."""
    flattened = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        phase=RuntimePhase.FLAT,
        pending_entry_bars=0,
        cooldown_bars_remaining=cooldown_bars,
        clear_pending_levels=True,
    )
    await store.save_deployment(flattened)
    return await store.get_deployment(snapshot.deployment.id)


async def _reprice_entry(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    prior: Order,
) -> DeploymentSnapshot:
    """Submit a replacement post-only buy at the current maker price."""
    try:
        price = broker.maker_limit_price(product_id=product.product_id, mark=candle.close)
    except BrokerError:
        paused = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Maker entry price is unavailable for repricing.",
            phase=RuntimePhase.FLAT,
            pending_entry_bars=0,
            clear_pending_levels=True,
        )
        await store.save_deployment(paused)
        return await store.get_deployment(snapshot.deployment.id)
    reset = with_runtime(snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0)
    await store.save_deployment(reset)
    await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=prior.quantity,
        price=price,
        candle=candle,
    )
    pending = with_runtime(
        reset, updated_at=utc_now(), phase=RuntimePhase.PENDING_ENTRY, pending_entry_bars=0
    )
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


async def _marketable_exit(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    purpose: IntentPurpose,
    price: Decimal,
) -> DeploymentSnapshot:
    """Cancel resting exits, then submit a marketable sell of the open position."""
    position = snapshot.position
    if position is None:
        return snapshot
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=purpose,
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=position.quantity,
        price=price,
        candle=candle,
    )
    if order.status is OrderStatus.FILLED:
        snapshot = await _apply_immediate_exit_fill(snapshot, order=order, store=store)
    cooled = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        cooldown_bars_remaining=strategy.entry.cooldown_bars,
    )
    await store.save_deployment(cooled)
    return await store.get_deployment(cooled.id)


async def _apply_immediate_exit_fill(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Apply a marketable sell fill that the broker reported as already complete."""
    fill = next(
        (
            item
            for item in (await store.get_deployment(snapshot.deployment.id)).fills
            if item.order_id == order.id
        ),
        None,
    )
    if fill is None:
        return snapshot
    return await apply_fill(
        await store.get_deployment(snapshot.deployment.id),
        fill=fill,
        order=order,
        store=store,
    )


async def _ensure_take_profit(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest a post-only take-profit sell when the position has none."""
    position = snapshot.position
    if position is None:
        return snapshot
    if _open_side(snapshot.orders, OrderSide.SELL) is not None:
        return snapshot
    await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.TAKE_PROFIT,
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=position.quantity,
        price=position.target_price,
        candle=candle,
    )
    pending = with_runtime(
        snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
    )
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


async def _maybe_enter(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Place a post-only buy when flat, off cooldown, and the entry condition matches."""
    deployment = snapshot.deployment
    if deployment.phase is not RuntimePhase.FLAT or deployment.cooldown_bars_remaining > 0:
        return snapshot
    outcome = evaluate_latest_entry(strategy, candles)
    signaled = with_runtime(deployment, updated_at=utc_now(), last_signal=outcome.value)
    await store.save_deployment(signaled)
    snapshot = await store.get_deployment(deployment.id)
    if outcome is not EntryConditionOutcome.MATCHED:
        return snapshot
    return await _submit_sized_entry(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        candle=candle,
        broker=broker,
        store=store,
    )


async def _submit_sized_entry(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Size a long and rest a post-only entry when cash and ATR allow it."""
    atr = latest_atr(strategy, candles)
    if atr is None:
        return snapshot
    try:
        entry_price = broker.maker_limit_price(product_id=product.product_id, mark=candle.close)
    except BrokerError:
        paused = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Maker entry price is unavailable.",
        )
        await store.save_deployment(paused)
        return await store.get_deployment(snapshot.deployment.id)
    sized = size_long_entry(
        strategy=strategy,
        cash=snapshot.deployment.cash,
        entry_price=entry_price,
        atr=atr,
        product=product,
    )
    if sized is None:
        return snapshot
    pending = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_entry_bars=0,
        pending_stop_price=sized.stop_price,
        pending_target_price=sized.target_price,
    )
    await store.save_deployment(pending)
    await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=sized.quantity,
        price=sized.entry_price,
        candle=candle,
    )
    return await store.get_deployment(snapshot.deployment.id)


async def _cancel_open_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> None:
    """Cancel every resting order before a marketable exit."""
    for order in snapshot.orders:
        if order.status is OrderStatus.OPEN:
            await _cancel_one_order(order, broker=broker, store=store)


async def _cancel_one_order(
    order: Order, *, broker: Broker, store: ExecutionStore
) -> DeploymentSnapshot:
    """Cancel one open order when the venue id is known."""
    if order.venue_order_id is None:
        return await store.get_deployment(order.deployment_id)
    result = await broker.cancel_order(
        venue_order_id=order.venue_order_id,
        client_order_id=order.client_order_id,
    )
    await store.save_order(
        replace(
            order,
            status=result.status,
            updated_at=utc_now(),
            reject_reason=result.reject_reason,
        )
    )
    return await store.get_deployment(order.deployment_id)


async def _ensure_take_profit_if_open(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest a take-profit when a skipped bar still has an unprotected open long."""
    if snapshot.deployment.phase is not RuntimePhase.OPEN:
        return snapshot
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


def _open_side(orders: Sequence[Order], side: OrderSide) -> Order | None:
    """Return the first open order on one side, if any."""
    return next(
        (order for order in orders if order.status is OrderStatus.OPEN and order.side is side),
        None,
    )
