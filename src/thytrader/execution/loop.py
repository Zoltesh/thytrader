"""One closed-candle cycle for a deployed strategy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError
from thytrader.execution.geometry import (
    entry_order_side,
    exit_order_side,
    paper_stop_fill_price,
    paper_stop_hit,
)
from thytrader.execution.ids import utc_now
from thytrader.execution.ledger import PAPER_MAKER_FEE_RATE
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    with_runtime,
)
from thytrader.execution.signals import evaluate_latest_entry, latest_atr, named_atr
from thytrader.execution.sizing import size_entry
from thytrader.execution.submit import submit_intent
from thytrader.execution.trailing import ratcheted_long_stop, ratcheted_short_stop
from thytrader.research.multi_timeframe import htf_bars_closed_at_or_before, ltf_close
from thytrader.research.signal_evaluator import SignalEvaluationError
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry
from thytrader.risk.models import RiskDecision, compiled_default_risk_policy
from thytrader.strategies.models import atr_trailing_stop

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.execution.broker import Broker
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition

_ACTIVE = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}


async def process_closed_bar(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    live_base_available: Decimal | None = None,
) -> DeploymentSnapshot:
    """Advance one running or paused deployment by exactly one newly closed candle."""
    if snapshot.deployment.status is DeploymentStatus.STOPPED or not candles:
        return snapshot
    if snapshot.deployment.status not in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}:
        return snapshot
    candle = candles[-1]
    deployment = snapshot.deployment
    if deployment.last_evaluated_bar == candle.starts_at:
        if deployment.phase in _IN_MARKET:
            return await _ensure_exit_protection(
                snapshot, candle=candle, product=product, broker=broker, store=store
            )
        return snapshot
    if deployment.cooldown_bars_remaining > 0:
        cooled = with_runtime(
            deployment,
            updated_at=utc_now(),
            cooldown_bars_remaining=deployment.cooldown_bars_remaining - 1,
        )
        await store.save_deployment(cooled)
        snapshot = await store.get_deployment(deployment.id)
    snapshot = await _match_resting_orders(
        snapshot,
        candle=candle,
        broker=broker,
        store=store,
        cooldown_bars=strategy.entry.cooldown_bars,
    )
    snapshot = await _manage_position(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
    )
    if snapshot.deployment.status is DeploymentStatus.RUNNING:
        snapshot = await _maybe_enter(
            snapshot,
            strategy=strategy,
            product=product,
            candles=candles,
            candle=candle,
            broker=broker,
            store=store,
            risk_policy=risk_policy or compiled_default_risk_policy(),
            portfolio=portfolio,
            htf_candles=htf_candles,
            indicator_timeframe_candles=indicator_timeframe_candles,
            live_base_available=live_base_available,
        )
    return await _persist_runtime(
        snapshot,
        store=store,
        last_evaluated_bar=candle.starts_at,
    )


async def cancel_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel every locally open order for a stopped or flattening deployment."""
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    return await store.get_deployment(snapshot.deployment.id)


async def _match_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
    cooldown_bars: int,
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
        snapshot = await apply_fill(
            snapshot, fill=fill, order=filled, store=store, cooldown_bars=cooldown_bars
        )
    return await store.get_deployment(snapshot.deployment.id)


async def apply_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Update cash and position from one fill and persist the result."""
    position = snapshot.position
    if position is None:
        return await _apply_entry_fill(snapshot, fill=fill, order=order, store=store)
    scaling_in = (position.side is PositionSide.LONG and order.side is OrderSide.BUY) or (
        position.side is PositionSide.SHORT and order.side is OrderSide.SELL
    )
    if scaling_in:
        return await _apply_scale_in_fill(
            snapshot, fill=fill, order=order, store=store, position=position
        )
    return await _apply_exit_fill(
        snapshot, fill=fill, order=order, store=store, cooldown_bars=cooldown_bars
    )


async def _apply_entry_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Open a long from a buy fill or a short from a sell fill."""
    deployment = snapshot.deployment
    now = utc_now()
    side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
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
    entered_bar = fill.filled_at.astimezone(UTC).replace(second=0, microsecond=0)
    position = Position(
        deployment_id=deployment.id,
        quantity=fill.quantity,
        entry_price=fill.price,
        stop_price=stop,
        target_price=target,
        entered_bar=entered_bar,
        updated_at=now,
        side=side,
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


async def _apply_scale_in_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot:
    """Add to an existing position on the same side."""
    deployment = snapshot.deployment
    now = utc_now()
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    quantity = position.quantity + fill.quantity
    entry_price = (
        (position.entry_price * position.quantity) + (fill.price * fill.quantity)
    ) / quantity
    updated_position = replace(position, quantity=quantity, entry_price=entry_price, updated_at=now)
    updated = with_runtime(
        deployment,
        updated_at=now,
        cash=cash,
        phase=RuntimePhase.OPEN if deployment.phase is RuntimePhase.FLAT else deployment.phase,
    )
    await store.save_position(updated_position, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


async def _apply_exit_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Reduce or flatten the open position after a covering fill."""
    deployment = snapshot.deployment
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    position = snapshot.position
    if position is not None and fill.quantity < position.quantity:
        remaining = replace(
            position,
            quantity=position.quantity - fill.quantity,
            updated_at=utc_now(),
        )
        updated = with_runtime(deployment, updated_at=utc_now(), cash=cash)
        await store.save_position(remaining, deployment_id=deployment.id)
        await store.save_deployment(updated)
        return await store.get_deployment(deployment.id)
    updated = with_runtime(
        deployment,
        updated_at=utc_now(),
        cash=cash,
        phase=RuntimePhase.FLAT,
        bars_held=0,
        pending_entry_bars=0,
        cooldown_bars_remaining=max(cooldown_bars, 0),
        clear_pending_levels=True,
    )
    await store.save_position(None, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


def _cash_after_fill(cash: Decimal, *, fill: Fill, order_side: OrderSide) -> Decimal:
    """Apply quote cash for a spot buy (debit) or sell (credit)."""
    notional = fill.price * fill.quantity
    if order_side is OrderSide.BUY:
        return cash - notional - fill.fee
    return cash + notional - fill.fee


async def _manage_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
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
    if deployment.phase not in _IN_MARKET or position is None:
        return snapshot
    if position.entered_bar != candle.starts_at:
        bars_held = deployment.bars_held + 1
        updated = with_runtime(deployment, updated_at=utc_now(), bars_held=bars_held)
        await store.save_deployment(updated)
        snapshot = await store.get_deployment(deployment.id)
        if snapshot.position is None:
            return snapshot
    snapshot = await _apply_trailing(
        snapshot, strategy=strategy, candles=candles, candle=candle, product=product, store=store
    )
    return await _protect_open_position(
        snapshot, strategy=strategy, candle=candle, product=product, broker=broker, store=store
    )


async def _apply_trailing(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Ratchet an ATR trailing stop after the fill bar; no-op when disabled."""
    position = snapshot.position
    policy = atr_trailing_stop(strategy.exits)
    if position is None or policy is None:
        return snapshot
    atr = named_atr(strategy, candles, policy.atr_indicator)
    if position.side is PositionSide.SHORT:
        state = ratcheted_short_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_low=candle.low,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=product.price_increment,
            ratchet=position.entered_bar < candle.starts_at,
        )
    else:
        state = ratcheted_long_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_high=candle.high,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=product.price_increment,
            ratchet=position.entered_bar < candle.starts_at,
        )
    if state.stop_price == position.stop_price and state.trail_extreme == position.trail_extreme:
        return snapshot
    await store.save_position(
        replace(
            position,
            stop_price=state.stop_price,
            trail_extreme=state.trail_extreme,
            updated_at=utc_now(),
        ),
        deployment_id=snapshot.deployment.id,
    )
    return await store.get_deployment(snapshot.deployment.id)


async def _protect_open_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Apply paper synthetic stops or live venue brackets after trailing."""
    position = snapshot.position
    if position is None:
        return snapshot
    bars_held = snapshot.deployment.bars_held
    timed_out = bars_held >= strategy.exits.time_exit.max_bars_held
    live = snapshot.deployment.mode is DeploymentMode.LIVE
    if live and not timed_out:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    paper_stop = paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price)
    if (not live and paper_stop) or timed_out:
        purpose = IntentPurpose.TIME_EXIT if timed_out else IntentPurpose.STOP
        price = (
            candle.close
            if timed_out
            else paper_stop_fill_price(
                side=position.side, candle=candle, stop_price=position.stop_price
            )
        )
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=purpose,
            price=price,
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


async def _ensure_live_bracket(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest one venue OCO bracket, replacing it when the working stop ratchets."""
    position = snapshot.position
    if position is None:
        return snapshot
    cover = exit_order_side(position.side)
    if _attached_entry_covers(snapshot, position):
        return await _mark_pending_exit(snapshot, store=store)
    existing = _active_side(snapshot.orders, cover)
    if existing is not None:
        matching = (
            existing.kind is OrderKind.TRIGGER_BRACKET
            and existing.price == position.target_price
            and existing.stop_trigger_price == position.stop_price
        )
        if matching:
            return await _mark_pending_exit(snapshot, store=store)
        snapshot = await _cancel_one_order(existing, broker=broker, store=store)
        remaining = _active_side(snapshot.orders, cover)
        if remaining is not None and remaining.status is not OrderStatus.CANCELED:
            return await _pause(
                snapshot,
                store=store,
                detail="Could not cancel the resting exit before replacing the live bracket.",
            )
    if _filled_attached_entry(snapshot, position) is not None:
        return await _pause(
            snapshot,
            store=store,
            detail="Cannot replace an attached live bracket without a recorded child order.",
        )
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.BRACKET,
        side=cover,
        kind=OrderKind.TRIGGER_BRACKET,
        quantity=position.quantity,
        price=position.target_price,
        stop_trigger_price=position.stop_price,
        candle=candle,
    )
    if order.status is OrderStatus.OPEN:
        return await _mark_pending_exit(
            await store.get_deployment(snapshot.deployment.id), store=store
        )
    if order.status in {OrderStatus.UNKNOWN, OrderStatus.PENDING}:
        return await _pause(
            await store.get_deployment(snapshot.deployment.id),
            store=store,
            detail="Live bracket submit is unconfirmed.",
        )
    return await _pause(
        await store.get_deployment(snapshot.deployment.id),
        store=store,
        detail="Live bracket could not be rested on an open position.",
    )


async def _mark_pending_exit(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Record that an exit order is working without changing cash or inventory."""
    pending = with_runtime(
        snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
    )
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


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
    open_entry = _active_entry(snapshot)
    if open_entry is None:
        return await _flatten_pending(snapshot, store=store, cooldown_bars=1)
    if open_entry.status is not OrderStatus.OPEN or open_entry.venue_order_id is None:
        return await _pause(
            snapshot,
            store=store,
            detail="Entry order is unconfirmed; reconcile before retrying.",
        )
    if waited < strategy.execution.max_entry_wait_bars:
        waited_state = with_runtime(deployment, updated_at=utc_now(), pending_entry_bars=waited)
        await store.save_deployment(waited_state)
        return await store.get_deployment(deployment.id)
    snapshot = await _cancel_one_order(open_entry, broker=broker, store=store)
    remaining = _active_entry(snapshot)
    if remaining is not None and remaining.status is not OrderStatus.CANCELED:
        return await _pause(
            snapshot,
            store=store,
            detail="Unfilled entry could not be canceled before retrying.",
        )
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
    """Submit a replacement post-only entry at the current maker price."""
    try:
        price = broker.maker_limit_price(
            product_id=product.product_id, mark=candle.close, side=prior.side
        )
    except BrokerError:
        return await _pause(
            snapshot,
            store=store,
            detail="Maker entry price is unavailable for repricing.",
            phase=RuntimePhase.FLAT,
        )
    reset = with_runtime(snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0)
    await store.save_deployment(reset)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=prior.side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=prior.quantity,
        price=price,
        candle=candle,
        stop_trigger_price=prior.stop_trigger_price,
        take_profit_price=prior.take_profit_price,
    )
    if order.status is OrderStatus.UNKNOWN:
        return await _pause(
            await store.get_deployment(snapshot.deployment.id),
            store=store,
            detail="Repriced entry submit is unconfirmed.",
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
    """Cancel resting exits, then submit a marketable cover of the open position."""
    position = snapshot.position
    if position is None:
        return snapshot
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if _active_side(snapshot.orders, OrderSide.BUY) or _active_side(
        snapshot.orders, OrderSide.SELL
    ):
        return await _pause(
            snapshot,
            store=store,
            detail="Could not cancel resting orders before a marketable exit.",
        )
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=purpose,
        side=exit_order_side(position.side),
        kind=OrderKind.MARKETABLE,
        quantity=position.quantity,
        price=price,
        candle=candle,
    )
    if order.status is OrderStatus.FILLED:
        return await _apply_immediate_exit_fill(
            snapshot,
            order=order,
            store=store,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
    return await _pause(
        await store.get_deployment(snapshot.deployment.id),
        store=store,
        detail="Marketable exit was not confirmed filled.",
    )


async def _apply_immediate_exit_fill(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Apply a marketable cover fill that the broker reported as already complete."""
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
        cooldown_bars=cooldown_bars,
    )


async def _ensure_exit_protection(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest paper take-profit or a live venue bracket when already evaluated."""
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


async def _ensure_take_profit(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest a post-only take-profit when the position has none."""
    position = snapshot.position
    if position is None:
        return snapshot
    cover = exit_order_side(position.side)
    if _active_side(snapshot.orders, cover) is not None:
        pending = with_runtime(
            snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
        )
        await store.save_deployment(pending)
        return await store.get_deployment(snapshot.deployment.id)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.TAKE_PROFIT,
        side=cover,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=position.quantity,
        price=position.target_price,
        candle=candle,
    )
    if order.status is OrderStatus.OPEN:
        pending = with_runtime(
            snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
        )
        await store.save_deployment(pending)
        return await store.get_deployment(snapshot.deployment.id)
    if order.status in {OrderStatus.UNKNOWN, OrderStatus.PENDING}:
        return await _pause(
            await store.get_deployment(snapshot.deployment.id),
            store=store,
            detail="Take-profit submit is unconfirmed.",
        )
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
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    live_base_available: Decimal | None = None,
) -> DeploymentSnapshot:
    """Place a post-only entry when flat, off cooldown, and the entry condition matches."""
    deployment = snapshot.deployment
    if deployment.phase is not RuntimePhase.FLAT or deployment.cooldown_bars_remaining > 0:
        return snapshot
    if deployment.status is not DeploymentStatus.RUNNING:
        return snapshot
    visible_htf = htf_candles
    htf_filter = strategy.htf_filter
    if htf_filter is not None:
        visible_htf = htf_bars_closed_at_or_before(
            htf_candles,
            close_at=ltf_close(candle.starts_at, strategy.timeframe),
            htf_timeframe=htf_filter.timeframe,
        )
    try:
        outcome = evaluate_latest_entry(strategy, candles, visible_htf, indicator_timeframe_candles)
    except SignalEvaluationError as error:
        return await _pause(snapshot, store=store, detail=str(error))
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
        risk_policy=risk_policy,
        portfolio=portfolio,
        live_base_available=live_base_available,
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
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    live_base_available: Decimal | None = None,
) -> DeploymentSnapshot:
    """Size an entry and rest a post-only order when cash, ATR, and risk policy allow it."""
    atr = latest_atr(strategy, candles)
    if atr is None:
        return snapshot
    side = PositionSide(strategy.entry.side)
    open_side = entry_order_side(side)
    try:
        entry_price = broker.maker_limit_price(
            product_id=product.product_id, mark=candle.close, side=open_side
        )
    except BrokerError:
        return await _pause(snapshot, store=store, detail="Maker entry price is unavailable.")
    sized = size_entry(
        strategy=strategy,
        cash=snapshot.deployment.cash,
        entry_price=entry_price,
        atr=atr,
        product=product,
        fee_rate=PAPER_MAKER_FEE_RATE,
        side=side,
    )
    if sized is None:
        return snapshot
    if (
        side is PositionSide.SHORT
        and snapshot.deployment.mode is DeploymentMode.LIVE
        and (live_base_available is None or live_base_available < sized.quantity)
    ):
        return await _pause(
            snapshot,
            store=store,
            detail=(
                "INSUFFICIENT_BASE_FOR_SPOT_SHORT: Coinbase spot shorts require available base."
            ),
        )
    admitted = _entry_admitted(
        snapshot,
        product_id=product.product_id,
        notional=sized.notional,
        risk_policy=risk_policy,
        portfolio=portfolio,
    )
    if not admitted:
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
    attach = atr_trailing_stop(strategy.exits) is None
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=open_side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=sized.quantity,
        price=sized.entry_price,
        candle=candle,
        stop_trigger_price=sized.stop_price if attach else None,
        take_profit_price=sized.target_price if attach else None,
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if order.status is OrderStatus.UNKNOWN:
        return await _pause(snapshot, store=store, detail="Entry submit is unconfirmed.")
    if order.status is OrderStatus.REJECTED:
        return await _flatten_pending(
            snapshot, store=store, cooldown_bars=max(strategy.entry.cooldown_bars, 1)
        )
    return snapshot


def _entry_admitted(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
) -> bool:
    """Return whether the active risk policy allows this sized entry."""
    live_cash = None
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        live_cash = snapshot.deployment.cash
    verdict = evaluate_new_entry(
        risk_policy,
        mode=snapshot.deployment.mode,
        proposed=ProposedEntry(
            product_id=product_id,
            strategy_id=snapshot.deployment.strategy_id,
            notional=notional,
        ),
        snapshots=_portfolio_with_current(portfolio, snapshot),
        live_quote_cash=live_cash,
    )
    return verdict.decision is RiskDecision.ALLOW


def _portfolio_with_current(
    portfolio: Sequence[DeploymentSnapshot],
    snapshot: DeploymentSnapshot,
) -> tuple[DeploymentSnapshot, ...]:
    """Overlay this deployment's latest snapshot onto occupied peers."""
    others = tuple(item for item in portfolio if item.deployment.id != snapshot.deployment.id)
    return (*others, snapshot)


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


async def _pause(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    detail: str,
    phase: RuntimePhase | None = None,
) -> DeploymentSnapshot:
    """Pause when venue state cannot be reconciled safely."""
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail=detail,
        phase=snapshot.deployment.phase if phase is None else phase,
        pending_entry_bars=0 if phase is RuntimePhase.FLAT else None,
        clear_pending_levels=phase is RuntimePhase.FLAT,
    )
    await store.save_deployment(paused)
    return await store.get_deployment(snapshot.deployment.id)


async def _persist_runtime(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    last_evaluated_bar: datetime,
) -> DeploymentSnapshot:
    """Write runtime fields without clobbering a concurrent operator status."""
    latest = await store.get_deployment(snapshot.deployment.id)
    operator = latest.deployment.status
    status = operator
    if (
        snapshot.deployment.status is DeploymentStatus.PAUSED
        and operator is not DeploymentStatus.STOPPED
    ):
        status = DeploymentStatus.PAUSED
    updated = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        last_evaluated_bar=last_evaluated_bar,
        status=status,
    )
    await store.save_deployment(updated)
    return await store.get_deployment(updated.id)


def _active_side(orders: Sequence[Order], side: OrderSide) -> Order | None:
    """Return the first active order on one side, if any."""
    return next(
        (order for order in orders if order.status in _ACTIVE and order.side is side),
        None,
    )


def _active_entry(snapshot: DeploymentSnapshot) -> Order | None:
    """Return the working entry order, preferring purpose-tagged intents."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    if entry_ids:
        return next(
            (
                order
                for order in snapshot.orders
                if order.intent_id in entry_ids and order.status in _ACTIVE
            ),
            None,
        )
    return next(
        (
            order
            for order in snapshot.orders
            if order.status in _ACTIVE
            and order.kind in {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE}
        ),
        None,
    )


def _fill_opened_position(fill: Fill, position: Position) -> bool:
    """True when this fill is the entry that opened the current position."""
    entered = fill.filled_at.astimezone(UTC).replace(second=0, microsecond=0)
    return entered == position.entered_bar and fill.price == position.entry_price


def _filled_attached_entry(snapshot: DeploymentSnapshot, position: Position) -> Order | None:
    """Return the filled entry that opened this position with an attached venue bracket."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    fills_by_order: dict[UUID, list[Fill]] = {}
    for fill in snapshot.fills:
        fills_by_order.setdefault(fill.order_id, []).append(fill)
    for order in snapshot.orders:
        if order.status is not OrderStatus.FILLED:
            continue
        if order.kind not in {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE}:
            continue
        if order.stop_trigger_price is None or order.take_profit_price is None:
            continue
        if entry_ids and order.intent_id not in entry_ids:
            continue
        order_fills = fills_by_order.get(order.id, ())
        if not any(_fill_opened_position(item, position) for item in order_fills):
            continue
        return order
    return None


def _attached_entry_covers(snapshot: DeploymentSnapshot, position: Position) -> bool:
    """True when a filled attached entry still matches the working stop and target."""
    entry = _filled_attached_entry(snapshot, position)
    if entry is None:
        return False
    return (
        entry.stop_trigger_price == position.stop_price
        and entry.take_profit_price == position.target_price
    )
