"""One closed-candle cycle for a deployed strategy."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import inspect
from typing import TYPE_CHECKING, Literal

from thytrader.execution.attached import (
    attached_entry_covers as _attached_entry_covers,
    remaining_quantity,
)
from thytrader.execution.broker import BrokerError
from thytrader.execution.capital import live_capital_base, live_sizing_cash, refresh_performance
from thytrader.execution.fill_ledger import ingest_fill, prior_fills_for_order
from thytrader.execution.freshness import entry_prerequisites, signal_still_valid
from thytrader.execution.geometry import (
    entry_bar_bucket,
    entry_order_side,
    exit_order_side,
    paper_stop_fill_price,
    paper_stop_hit,
)
from thytrader.execution.ids import utc_now
from thytrader.execution.ledger import PAPER_MAKER_FEE_RATE, effective_paper_fee_rates
from thytrader.execution.lifecycle import can_reprice_risk_up, entries_allowed
from thytrader.execution.models import (
    Deployment,
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
    resolved_product_id,
    snapshot_positions,
    with_runtime,
)
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.signals import evaluate_latest_entry, latest_atr, named_atr
from thytrader.execution.sizing import SizedEntry, size_entry, size_pyramid_add
from thytrader.execution.submit import submit_intent
from thytrader.execution.trade_reason_scope import current_trade_reason_scope
from thytrader.execution.trailing import ratcheted_long_stop, ratcheted_short_stop
from thytrader.market_data.models import parse_candle_interval
from thytrader.research.multi_timeframe import htf_bars_closed_at_or_before, ltf_close
from thytrader.research.signal_evaluator import SignalEvaluationError
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.breakers import EntryObservation, breaker_pause_detail
from thytrader.risk.exposure import snapshot_has_residual_exposure
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry, evaluate_runtime_breakers
from thytrader.risk.models import (
    RiskDecision,
    RiskReasonCode,
    RiskVerdict,
    compiled_default_risk_policy,
    pauses_risk_increasing,
)
from thytrader.strategies.models import atr_trailing_stop, can_pyramid_add

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition

_ACTIVE = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}


async def maintain_open_inventory(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Reconcile resting orders and ensure protection between closed bars."""
    if not candles:
        return snapshot
    return await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=bind_paper_broker_fees(broker, snapshot.deployment),
        store=store,
        allow_new_entries=False,
    )


async def flatten_stopped_residual(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Marketably exit open inventory on a flatten command, then cancel remainders."""
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    if snapshot.position is not None and candles:
        candle = candles[-1]
        snapshot = await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.STOP,
            price=candle.close,
        )
    return await cancel_resting_orders(snapshot, broker=broker, store=store)


async def cancel_risk_increasing_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel working entries while leaving protective brackets in place."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    for order in tuple(snapshot.orders):
        if order.status not in _ACTIVE:
            continue
        if order.kind is OrderKind.TRIGGER_BRACKET:
            continue
        if entry_ids and order.intent_id not in entry_ids:
            continue
        snapshot = await _cancel_one_order(order, broker=broker, store=store)
    return await store.get_deployment(snapshot.deployment.id)


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
    marks: Mapping[str, Decimal] | None = None,
    fee_profile: FeeProfile | None = None,
    allow_new_entries: bool = True,
) -> DeploymentSnapshot:
    """Advance one running, paused, or residual-stopped deployment by one closed candle."""
    if not candles:
        return snapshot
    stopped = snapshot.deployment.status is DeploymentStatus.STOPPED
    if stopped and not snapshot_has_residual_exposure(snapshot):
        return snapshot
    if snapshot.deployment.status not in {
        DeploymentStatus.RUNNING,
        DeploymentStatus.PAUSED,
        DeploymentStatus.STOPPED,
    }:
        return snapshot
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    candle = candles[-1]
    deployment = snapshot.deployment
    if deployment.last_evaluated_bar == candle.starts_at:
        if deployment.phase in _IN_MARKET or snapshot.position is not None:
            snapshot = await _ensure_exit_protection(
                snapshot, candle=candle, product=product, broker=broker, store=store
            )
        return await _persist_performance(
            snapshot,
            store=store,
            mark_price=candle.close,
            marks=marks,
            product_id=product.product_id,
        )
    if deployment.cooldown_bars_remaining > 0 and not stopped:
        cooled = with_runtime(
            deployment,
            updated_at=utc_now(),
            cooldown_bars_remaining=deployment.cooldown_bars_remaining - 1,
        )
        await store.save_deployment(cooled)
        snapshot = await store.get_deployment(deployment.id)
    policy = risk_policy or compiled_default_risk_policy()
    snapshot = await _match_resting_orders(
        snapshot,
        candle=candle,
        broker=broker,
        store=store,
        cooldown_bars=strategy.entry.cooldown_bars,
        timeframe=deployment.timeframe or strategy.timeframe,
    )
    snapshot = await _manage_position(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        marks=marks,
        live_base_available=live_base_available,
        risk_policy=policy,
        portfolio=portfolio,
    )
    if snapshot.deployment.status is DeploymentStatus.RUNNING:
        snapshot = await _apply_circuit_breakers(
            snapshot,
            candle=candle,
            store=store,
            risk_policy=policy,
            portfolio=portfolio,
            marks=marks,
        )
    may_enter = (
        allow_new_entries
        and entries_allowed(snapshot.deployment)
        and snapshot.deployment.status is DeploymentStatus.RUNNING
    )
    if may_enter:
        snapshot = await _maybe_enter(
            snapshot,
            strategy=strategy,
            product=product,
            candles=candles,
            candle=candle,
            broker=broker,
            store=store,
            risk_policy=policy,
            portfolio=portfolio,
            htf_candles=htf_candles,
            indicator_timeframe_candles=indicator_timeframe_candles,
            live_base_available=live_base_available,
            marks=marks,
            fee_profile=fee_profile,
        )
    snapshot = await _persist_performance(
        snapshot,
        store=store,
        mark_price=candle.close,
        marks=marks,
        product_id=product.product_id,
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
    timeframe: str | None = None,
) -> DeploymentSnapshot:
    """Apply paper or local fills for resting limits against the closed candle."""
    for order in snapshot.orders:
        if order.status is not OrderStatus.OPEN:
            continue
        fill = broker.match_open_order(order, candle)
        if fill is None:
            continue
        result = await ingest_fill(
            snapshot,
            fill=fill,
            order=order,
            store=store,
            cooldown_bars=cooldown_bars,
            timeframe=timeframe,
        )
        snapshot = result.snapshot
    return await store.get_deployment(snapshot.deployment.id)


async def apply_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int = 0,
    timeframe: str | None = None,
) -> DeploymentSnapshot:
    """Update cash and position from one fill and persist the result."""
    result = await ingest_fill(
        snapshot,
        fill=fill,
        order=order,
        store=store,
        cooldown_bars=cooldown_bars,
        timeframe=timeframe or snapshot.deployment.timeframe,
    )
    return result.snapshot


async def _apply_entry_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    timeframe: str | None = None,
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
    bar_timeframe = timeframe or deployment.timeframe
    if bar_timeframe is None:
        paused = with_runtime(
            deployment,
            updated_at=now,
            cash=cash,
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Entry fill is missing a deployment timeframe for bar bucketing.",
            phase=RuntimePhase.FLAT,
            clear_pending_levels=True,
        )
        await store.save_deployment(paused)
        await store.save_position(None, deployment_id=deployment.id)
        return await store.get_deployment(deployment.id)
    entered_bar = entry_bar_bucket(fill.filled_at, bar_timeframe)
    position = Position(
        deployment_id=deployment.id,
        quantity=fill.quantity,
        entry_price=fill.price,
        stop_price=stop,
        target_price=target,
        entered_bar=entered_bar,
        updated_at=now,
        side=side,
        product_id=order.product_id or deployment.product_id,
        add_count=1,
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
    prior = prior_fills_for_order(snapshot, order.id)
    add_count = position.add_count + 1 if prior == 0 and order.pyramid_add else position.add_count
    updated_position = replace(
        position,
        quantity=quantity,
        entry_price=entry_price,
        updated_at=now,
        add_count=add_count,
    )
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
    marks: Mapping[str, Decimal] | None = None,
    live_base_available: Decimal | None = None,
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
) -> DeploymentSnapshot:
    """Exit on stop, take-profit fill wait, or time, and expire working entry remainders."""
    if _active_entry(snapshot) is not None:
        snapshot = await _manage_working_entry(
            snapshot,
            strategy=strategy,
            candles=candles,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            marks=marks,
            live_base_available=live_base_available,
            risk_policy=risk_policy,
            portfolio=portfolio,
        )
        if snapshot.deployment.status is DeploymentStatus.STOPPED:
            return snapshot
        if snapshot.position is None and snapshot.deployment.phase is RuntimePhase.FLAT:
            return snapshot
    checked = await _fail_closed_on_split_state(snapshot, store=store)
    if checked is not None:
        return checked
    return await _manage_open_position(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
    )


def split_pending_entry(snapshot: DeploymentSnapshot) -> bool:
    """Whether a pending-entry book has neither a working entry nor a position.

    A RUNNING or PAUSED book in PENDING_ENTRY phase always holds its working
    entry until cancel/flatten. Missing both means the entry lifecycle was
    skipped or fill economics never committed (ADR 0057 violation) — the
    deployment 01a0bb90 stuck-pending_entry condition.
    """
    deployment = snapshot.deployment
    if deployment.phase is not RuntimePhase.PENDING_ENTRY:
        return False
    if deployment.status not in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}:
        return False
    if deployment.mismatch_detail:
        return False
    return snapshot.position is None and _active_entry(snapshot) is None


def _split_state_detail(snapshot: DeploymentSnapshot) -> str:
    """Return the operator-facing reason a pending-entry book failed closed."""
    if any(
        order.status is OrderStatus.FILLED and order.kind is not OrderKind.TRIGGER_BRACKET
        for order in snapshot.orders
    ):
        return (
            "Filled entry order has no applied fill and no position; fill economics did not commit."
        )
    return (
        "Book is pending entry with no working entry order and no position; "
        "entry lifecycle did not advance."
    )


async def _fail_closed_on_split_state(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
) -> DeploymentSnapshot | None:
    """Pause split pending-entry state, or return None to continue the bar."""
    if not split_pending_entry(snapshot):
        return None
    return await _pause(
        snapshot,
        store=store,
        detail=_split_state_detail(snapshot),
    )


async def _manage_open_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Trail and protect an open book after pending-entry handling."""
    deployment = snapshot.deployment
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
    position = snapshot.position
    if position is None:
        return snapshot
    live = deployment.mode is DeploymentMode.LIVE
    timed_out = snapshot.deployment.bars_held >= strategy.exits.time_exit.max_bars_held
    if not live and not timed_out:
        stopped = await _paper_stop_exit_if_hit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            position=position,
        )
        if stopped is not None:
            return stopped
    snapshot = await _apply_trailing(
        snapshot, strategy=strategy, candles=candles, candle=candle, product=product, store=store
    )
    return await _protect_open_position(
        snapshot,
        strategy=strategy,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        skip_paper_stop=not live,
    )


async def _paper_stop_exit_if_hit(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot | None:
    """Exit on the pre-trail stop when a closed bar trades through it."""
    if not paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price):
        return None
    price = paper_stop_fill_price(side=position.side, candle=candle, stop_price=position.stop_price)
    return await _marketable_exit(
        snapshot,
        strategy=strategy,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        purpose=IntentPurpose.STOP,
        price=price,
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
    skip_paper_stop: bool = False,
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
    paper_stop = False
    if not skip_paper_stop:
        paper_stop = paper_stop_hit(
            side=position.side, candle=candle, stop_price=position.stop_price
        )
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
            and existing.quantity == position.quantity
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


async def _manage_working_entry(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    marks: Mapping[str, Decimal] | None,
    live_base_available: Decimal | None,
    risk_policy: RiskPolicyDefinition | None,
    portfolio: Sequence[DeploymentSnapshot],
) -> DeploymentSnapshot:
    """Wait, cancel, or reprice a working entry remainder in any phase."""
    deployment = snapshot.deployment
    waited = deployment.pending_entry_bars + 1
    open_entry = _active_entry(snapshot)
    has_position = snapshot.position is not None
    if open_entry is None:
        if has_position:
            cleared = with_runtime(deployment, updated_at=utc_now(), pending_entry_bars=0)
            await store.save_deployment(cleared)
            return await store.get_deployment(deployment.id)
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
    if strategy.execution.on_unfilled_entry == "reprice" and not can_reprice_risk_up(deployment):
        return snapshot
    snapshot = await _cancel_one_order(open_entry, broker=broker, store=store)
    remaining = _active_entry(snapshot)
    if remaining is not None and remaining.status is not OrderStatus.CANCELED:
        return await _pause(
            snapshot,
            store=store,
            detail="Unfilled entry could not be canceled before retrying.",
        )
    if strategy.execution.on_unfilled_entry == "reprice" and can_reprice_risk_up(
        snapshot.deployment
    ):
        phase = RuntimePhase.OPEN if has_position else RuntimePhase.PENDING_ENTRY
        return await _reprice_entry(
            snapshot,
            strategy=strategy,
            candles=candles,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            prior=open_entry,
            marks=marks,
            live_base_available=live_base_available,
            phase=phase,
            risk_policy=risk_policy,
            portfolio=portfolio,
        )
    if has_position:
        abandoned = with_runtime(snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0)
        await store.save_deployment(abandoned)
        return await store.get_deployment(snapshot.deployment.id)
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
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    prior: Order,
    marks: Mapping[str, Decimal] | None = None,
    live_base_available: Decimal | None = None,
    phase: RuntimePhase = RuntimePhase.PENDING_ENTRY,
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
) -> DeploymentSnapshot:
    """Submit a replacement post-only entry at remaining qty after sizing and risk re-admission."""
    remaining_qty = remaining_quantity(prior)
    if remaining_qty <= 0 or not can_reprice_risk_up(snapshot.deployment):
        return snapshot
    is_pyramid = phase is RuntimePhase.OPEN or snapshot.position is not None
    try:
        entry_price = await _await_maker_limit(
            broker, product_id=product.product_id, mark=candle.close, side=prior.side
        )
    except BrokerError:
        if is_pyramid:
            abandoned = with_runtime(
                snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0
            )
            await store.save_deployment(abandoned)
            return await store.get_deployment(snapshot.deployment.id)
        return await _pause(
            snapshot,
            store=store,
            detail="Maker entry price is unavailable for repricing.",
            phase=RuntimePhase.FLAT,
        )
    atr = latest_atr(strategy, candles)
    if atr is None:
        return snapshot
    side = PositionSide(strategy.entry.side)
    sized = _size_entry_or_add(
        snapshot,
        strategy=strategy,
        product=product,
        entry_price=entry_price,
        atr=atr,
        side=side,
        is_pyramid_add=is_pyramid,
    )
    if sized is None:
        return snapshot
    stop_price, target_price = _legal_reprice_geometry(
        side=side,
        entry_price=entry_price,
        stop_price=sized.stop_price,
        target_price=sized.target_price,
    )
    policy = risk_policy or compiled_default_risk_policy()
    admitted = _entry_verdict(
        snapshot,
        product_id=product.product_id,
        notional=entry_price * remaining_qty,
        risk_policy=policy,
        portfolio=portfolio,
        observation=_bar_observation(
            product_id=product.product_id,
            candle=candle,
            proposed_price=entry_price,
            marks=marks,
        ),
        is_pyramid_add=is_pyramid,
    )
    if admitted.decision is RiskDecision.DENY:
        if pauses_risk_increasing(admitted.reason_code):
            return await _pause_for_breaker(
                snapshot, store=store, portfolio=portfolio, verdict=admitted
            )
        return snapshot
    if (
        side is PositionSide.SHORT
        and snapshot.deployment.mode is DeploymentMode.LIVE
        and (live_base_available is None or live_base_available < remaining_qty)
    ):
        return await _pause(
            snapshot,
            store=store,
            detail=(
                "INSUFFICIENT_BASE_FOR_SPOT_SHORT: Coinbase spot shorts require available base."
            ),
        )
    return await _submit_repriced_entry(
        snapshot,
        broker=broker,
        store=store,
        product=product,
        candle=candle,
        prior=prior,
        remaining_qty=remaining_qty,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        is_pyramid=is_pyramid,
        phase=phase,
        attach=not is_pyramid and atr_trailing_stop(strategy.exits) is None,
    )


async def _submit_repriced_entry(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product: MarketProduct,
    candle: Candle,
    prior: Order,
    remaining_qty: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    is_pyramid: bool,
    phase: RuntimePhase,
    attach: bool,
) -> DeploymentSnapshot:
    """Persist pending levels and rest the replacement maker order."""
    reset = with_runtime(snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0)
    if not is_pyramid:
        reset = with_runtime(
            reset,
            updated_at=utc_now(),
            pending_stop_price=stop_price,
            pending_target_price=target_price,
        )
    await store.save_deployment(reset)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=prior.side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=remaining_qty,
        price=entry_price,
        candle=candle,
        stop_trigger_price=stop_price if attach else None,
        take_profit_price=target_price if attach else None,
        pyramid_add=is_pyramid,
        idempotency_key=_signal_intent_key(
            snapshot.deployment.id, IntentPurpose.ENTRY, candle.starts_at, product.product_id
        ),
    )
    if order.status is OrderStatus.UNKNOWN:
        return await _pause(
            await store.get_deployment(snapshot.deployment.id),
            store=store,
            detail="Repriced entry submit is unconfirmed.",
        )
    pending = with_runtime(reset, updated_at=utc_now(), phase=phase, pending_entry_bars=0)
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


def _legal_reprice_geometry(
    *,
    side: PositionSide,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
) -> tuple[Decimal, Decimal]:
    """Preserve remaining qty's legal stop/target; drop an obsolete target below a new buy."""
    if side is PositionSide.LONG and target_price <= entry_price:
        width = entry_price - stop_price
        if width <= 0:
            width = entry_price * Decimal("0.01")
        return stop_price, entry_price + width
    if side is PositionSide.SHORT and target_price >= entry_price:
        width = stop_price - entry_price
        if width <= 0:
            width = entry_price * Decimal("0.01")
        return stop_price, entry_price - width
    return stop_price, target_price


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


def _entry_attempt_mode(
    snapshot: DeploymentSnapshot, strategy: StrategyDefinition
) -> Literal["new", "pyramid"] | None:
    """Return whether this bar may open a book, add to one, or should skip."""
    deployment = snapshot.deployment
    if not entries_allowed(deployment):
        return None
    if deployment.phase is RuntimePhase.FLAT:
        if deployment.cooldown_bars_remaining > 0:
            return None
        if (
            _document_open_book_count(snapshot)
            >= strategy.portfolio_limits.max_concurrent_positions
        ):
            return None
        return "new"
    if (
        deployment.phase is RuntimePhase.OPEN
        and snapshot.position is not None
        and _active_entry(snapshot) is None
    ):
        return "pyramid"
    return None


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
    marks: Mapping[str, Decimal] | None = None,
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Place a post-only entry when flat, or a same-side add when pyramiding allows it."""
    mode = _entry_attempt_mode(snapshot, strategy)
    if mode is None:
        return snapshot
    pyramid_add = mode == "pyramid"
    deployment = snapshot.deployment
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
    now = utc_now()
    signaled = with_runtime(
        deployment,
        updated_at=now,
        last_signal=outcome.value,
        last_signal_event_at=candle.starts_at,
        last_signal_processed_at=now,
    )
    await store.save_deployment(signaled)
    snapshot = await store.get_deployment(deployment.id)
    position = snapshot.position
    if outcome is not EntryConditionOutcome.MATCHED:
        return snapshot
    evaluated_at = candle.starts_at + parse_candle_interval(strategy.timeframe).duration
    if not signal_still_valid(
        candle=candle,
        timeframe=strategy.timeframe,
        now=evaluated_at,
        current_quote=candle.close,
    ):
        return snapshot
    fresh = entry_prerequisites(
        product=product,
        candle=candle,
        now=evaluated_at,
        timeframe=strategy.timeframe,
        venue_balance_known=(
            live_sizing_cash(snapshot.deployment) is not None
            or snapshot.deployment.mode is DeploymentMode.PAPER
        ),
    )
    if fresh.decision is RiskDecision.DENY:
        return snapshot
    if pyramid_add:
        if position is None:
            return snapshot
        if not can_pyramid_add(
            strategy=strategy,
            side=position.side.value,
            entry_price=position.entry_price,
            mark=candle.close,
            add_count=position.add_count,
        ):
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
        marks=marks,
        is_pyramid_add=pyramid_add,
        fee_profile=fee_profile,
    )


def _size_entry_or_add(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    entry_price: Decimal,
    atr: Decimal,
    side: PositionSide,
    is_pyramid_add: bool,
    fee_profile: FeeProfile | None = None,
) -> SizedEntry | None:
    """Size a new book or a same-side add against remaining quote cash."""
    fee_rate = _entry_fee_rate(snapshot.deployment, fee_profile=fee_profile)
    sizing_cash = live_sizing_cash(snapshot.deployment)
    if sizing_cash is None:
        return None
    if not is_pyramid_add:
        return size_entry(
            strategy=strategy,
            cash=sizing_cash,
            entry_price=entry_price,
            atr=atr,
            product=product,
            fee_rate=fee_rate,
            side=side,
        )
    position = snapshot.position
    if position is None:
        return None
    return size_pyramid_add(
        strategy=strategy,
        cash=sizing_cash,
        entry_price=entry_price,
        existing_stop=position.stop_price,
        existing_target=position.target_price,
        product=product,
        fee_rate=fee_rate,
        side=side,
    )


def _runtime_for_admitted_entry(
    deployment: Deployment,
    *,
    sized: SizedEntry,
    strategy: StrategyDefinition,
    is_pyramid_add: bool,
) -> tuple[Deployment, bool]:
    """Stamp pending-entry or keep OPEN, and decide whether live brackets attach."""
    if is_pyramid_add:
        pending = with_runtime(
            deployment,
            updated_at=utc_now(),
            phase=RuntimePhase.OPEN,
            pending_entry_bars=0,
        )
        return pending, False
    pending = with_runtime(
        deployment,
        updated_at=utc_now(),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_entry_bars=0,
        pending_stop_price=sized.stop_price,
        pending_target_price=sized.target_price,
    )
    return pending, atr_trailing_stop(strategy.exits) is None


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
    marks: Mapping[str, Decimal] | None = None,
    is_pyramid_add: bool = False,
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Size an entry or same-side add and rest a post-only order when policy allows it."""
    atr = latest_atr(strategy, candles)
    if atr is None:
        return snapshot
    side = PositionSide(strategy.entry.side)
    open_side = entry_order_side(side)
    try:
        entry_price = await _await_maker_limit(
            broker, product_id=product.product_id, mark=candle.close, side=open_side
        )
    except BrokerError:
        return await _pause(snapshot, store=store, detail="Maker entry price is unavailable.")
    sized = _size_entry_or_add(
        snapshot,
        strategy=strategy,
        product=product,
        entry_price=entry_price,
        atr=atr,
        side=side,
        is_pyramid_add=is_pyramid_add,
        fee_profile=fee_profile,
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
    admitted = _entry_verdict(
        snapshot,
        product_id=product.product_id,
        notional=sized.notional,
        risk_policy=risk_policy,
        portfolio=portfolio,
        observation=_bar_observation(
            product_id=product.product_id,
            candle=candle,
            proposed_price=sized.entry_price,
            marks=marks,
        ),
        is_pyramid_add=is_pyramid_add,
    )
    if admitted.decision is RiskDecision.DENY:
        if pauses_risk_increasing(admitted.reason_code):
            return await _pause_for_breaker(
                snapshot, store=store, portfolio=portfolio, verdict=admitted
            )
        return snapshot
    pending, attach = _runtime_for_admitted_entry(
        snapshot.deployment, sized=sized, strategy=strategy, is_pyramid_add=is_pyramid_add
    )
    await store.save_deployment(pending)
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
        pyramid_add=is_pyramid_add,
        idempotency_key=_signal_intent_key(
            snapshot.deployment.id, IntentPurpose.ENTRY, candle.starts_at, product.product_id
        ),
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if order.status is OrderStatus.UNKNOWN:
        return await _pause(snapshot, store=store, detail="Entry submit is unconfirmed.")
    if order.status is OrderStatus.REJECTED:
        if is_pyramid_add:
            restored = with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                phase=RuntimePhase.OPEN,
                pending_entry_bars=0,
            )
            await store.save_deployment(restored)
            return await store.get_deployment(snapshot.deployment.id)
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
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
) -> bool:
    """Return whether the active risk policy allows this sized entry."""
    return (
        _entry_verdict(
            snapshot,
            product_id=product_id,
            notional=notional,
            risk_policy=risk_policy,
            portfolio=portfolio,
            observation=observation,
            is_pyramid_add=is_pyramid_add,
        ).decision
        is RiskDecision.ALLOW
    )


def _entry_verdict(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
) -> RiskVerdict:
    """Return the entry gate verdict for this sized order."""
    live_cash = live_capital_base(snapshot.deployment)
    if snapshot.deployment.mode is DeploymentMode.LIVE and live_cash is None:
        return RiskVerdict(
            decision=RiskDecision.DENY,
            reason_code=RiskReasonCode.VENUE_BALANCE_UNKNOWN,
            detail="Venue quote balance is unknown; new entries are disabled.",
        )
    verdict = evaluate_new_entry(
        risk_policy,
        mode=snapshot.deployment.mode,
        proposed=ProposedEntry(
            product_id=product_id,
            strategy_id=snapshot.deployment.strategy_id,
            notional=notional,
            is_pyramid_add=is_pyramid_add,
        ),
        snapshots=_portfolio_with_current(portfolio, snapshot),
        live_quote_cash=live_cash,
        observation=observation,
    )
    scope = current_trade_reason_scope()
    if scope is not None:
        scope.remember_risk(verdict)
    return verdict


def _document_open_book_count(snapshot: DeploymentSnapshot) -> int:
    """Count distinct product books and pending entries inside this document."""
    products: set[str] = set()
    for position in snapshot_positions(snapshot):
        products.add(resolved_product_id(position.product_id, snapshot.deployment))
    if snapshot.instrument_runtimes:
        for runtime in snapshot.instrument_runtimes:
            if runtime.phase in {
                RuntimePhase.OPEN,
                RuntimePhase.PENDING_ENTRY,
                RuntimePhase.PENDING_EXIT,
            }:
                products.add(runtime.product_id)
        return len(products)
    if snapshot.position is not None or snapshot.deployment.phase in {
        RuntimePhase.OPEN,
        RuntimePhase.PENDING_ENTRY,
        RuntimePhase.PENDING_EXIT,
    }:
        products.add(snapshot.deployment.product_id)
    return len(products)


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


def _bar_observation(
    *,
    product_id: str,
    candle: Candle,
    proposed_price: Decimal | None,
    marks: Mapping[str, Decimal] | None,
) -> EntryObservation:
    """Build breaker observation from this bar's close plus any sibling marks."""
    combined = dict(marks) if marks is not None else {}
    combined[product_id] = candle.close
    return EntryObservation(
        as_of=utc_now(),
        proposed_price=proposed_price,
        reference_price=candle.close,
        marks=combined,
    )


async def _apply_circuit_breakers(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    marks: Mapping[str, Decimal] | None,
) -> DeploymentSnapshot:
    """Pause when daily-loss or drawdown has already tripped before a new entry."""
    live_cash = live_capital_base(snapshot.deployment)
    verdict = evaluate_runtime_breakers(
        risk_policy,
        mode=snapshot.deployment.mode,
        snapshot=snapshot,
        snapshots=_portfolio_with_current(portfolio, snapshot),
        live_quote_cash=live_cash,
        observation=_bar_observation(
            product_id=snapshot.deployment.product_id,
            candle=candle,
            proposed_price=None,
            marks=marks,
        ),
    )
    if verdict.decision is RiskDecision.ALLOW:
        return snapshot
    if not pauses_risk_increasing(verdict.reason_code):
        return snapshot
    return await _pause_for_breaker(snapshot, store=store, portfolio=portfolio, verdict=verdict)


async def _pause_for_breaker(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    portfolio: Sequence[DeploymentSnapshot],
    verdict: RiskVerdict,
) -> DeploymentSnapshot:
    """Pause this book, and the whole mode when the daily-loss kill trips."""
    detail = breaker_pause_detail(verdict.reason_code, verdict.detail)
    latched_daily = verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    latched_dd = verdict.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
    if latched_daily or latched_dd:
        stamped = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            daily_loss_latched=True if latched_daily else None,
            drawdown_latched=True if latched_dd else None,
        )
        await store.save_deployment(stamped)
        snapshot = await store.get_deployment(snapshot.deployment.id)
    if verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT:
        await _pause_mode_running(
            store=store,
            mode=snapshot.deployment.mode,
            portfolio=_portfolio_with_current(portfolio, snapshot),
            detail=detail,
        )
        return await store.get_deployment(snapshot.deployment.id)
    return await _pause(snapshot, store=store, detail=detail)


async def _pause_mode_running(
    *,
    store: ExecutionStore,
    mode: DeploymentMode,
    portfolio: Sequence[DeploymentSnapshot],
    detail: str,
) -> None:
    """Pause every running deployment in this mode; exits on paused books continue."""
    for item in portfolio:
        deployment = item.deployment
        if deployment.mode is not mode or deployment.status is not DeploymentStatus.RUNNING:
            continue
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
        await store.save_deployment(paused)


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


async def _persist_performance(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    mark_price: Decimal,
    marks: Mapping[str, Decimal] | None = None,
    product_id: str,
) -> DeploymentSnapshot:
    """Stamp inventory cost, equity, HWM, and UTC day-open without changing phase."""
    combined: dict[str, Decimal] = dict(marks) if marks is not None else {}
    combined[product_id] = mark_price
    marked = refresh_performance(snapshot, marks=combined, now=utc_now())
    if marked == snapshot.deployment:
        return snapshot
    await store.save_deployment(marked)
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


async def _await_maker_limit(
    broker: Broker,
    *,
    product_id: str,
    mark: Decimal,
    side: OrderSide,
) -> Decimal:
    """Await maker_limit_price whether the broker implements it as async or sync.

    CoinbaseRestBroker keeps a sync implementation (owned by a sibling slice); paper
    and tests may be sync or async. Network I/O wrapping stays outside this helper.
    """
    result = broker.maker_limit_price(product_id=product_id, mark=mark, side=side)
    if inspect.isawaitable(result):
        awaited = await result
        if isinstance(awaited, Decimal):
            return awaited
        raise BrokerError("Maker entry price is unavailable.")
    if isinstance(result, Decimal):
        return result
    raise BrokerError("Maker entry price is unavailable.")


def _signal_intent_key(
    deployment_id: UUID, purpose: IntentPurpose, candle_starts_at: datetime, product_id: str
) -> str:
    """Stable command/signal identity used to deduplicate order intents."""
    stamp = candle_starts_at.strftime("%Y%m%dT%H%M")
    return f"{deployment_id}:{purpose.value}:{product_id}:{stamp}"[:128]


def _entry_fee_rate(deployment: Deployment, *, fee_profile: FeeProfile | None = None) -> Decimal:
    """Size entries with paper assumptions or the live Coinbase maker tier when available."""
    if deployment.mode is DeploymentMode.PAPER:
        maker_fee_rate, _taker_fee_rate = effective_paper_fee_rates(
            deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
        )
        return maker_fee_rate
    if fee_profile is not None:
        return fee_profile.maker_fee_rate
    return PAPER_MAKER_FEE_RATE
