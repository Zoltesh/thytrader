"""Protection of open positions: brackets, protective stops, take-profit, trailing.

Live bracket and stop-only protection (adopt, replace, submit, rejection pauses),
take-profit and paper stop exits, ATR trailing, and discretionary protection upkeep.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.attached import attached_entry_covers as _attached_entry_covers
from thytrader.execution.exit_guards import (
    BRACKET_NOT_RESTED_DETAIL,
    BRACKET_REPLACE_CANCEL_DETAIL,
    BRACKET_UNCONFIRMED_DETAIL,
    bracket_rejection,
    cancel_pending,
    exit_fill_pending,
    flatten_requested,
    rejection_latched,
)
from thytrader.execution.exits import _exit_economics_fault, _mark_pending_exit, _marketable_exit
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.runtime_ops import (
    _active_side,
    _adopt_venue_attached_child,
    _cancel_one_order,
    _match_resting_orders,
    _pause,
    _pause_rejected,
    _reconcile_stopped_live,
)
from thytrader.execution.signals import named_atr
from thytrader.execution.submit import submit_intent
from thytrader.execution.trailing import ratcheted_long_stop, ratcheted_short_stop
from thytrader.strategies.models import atr_trailing_stop
from thytrader.trading.geometry import (
    exit_order_side,
    paper_stop_fill_price,
    paper_stop_hit,
    protective_stop_limit_price,
)
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    Order,
    OrderKind,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.store import ExecutionStore


STOP_LIMIT_PRICE_DETAIL = (
    "Stop-only protection limit price is not positive at the venue price increment."
)


async def maintain_discretionary_protection(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    strategy: StrategyDefinition | None = None,
) -> DeploymentSnapshot:
    """Keep stored protection working without reversing a durable exit decision.

    Missing candles do nothing: a shutdown must not invent an exit price or
    cancel protection merely because the decision window is empty.
    """
    if not candles or snapshot.position is None:
        return snapshot
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    candle = candles[-1]
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        snapshot = await _match_resting_orders(
            snapshot,
            candle=candle,
            broker=broker,
            store=store,
            cooldown_bars=0,
            stop_first=True,
        )
    position = snapshot.position
    if position is None:
        return snapshot
    purpose = _due_exit_purpose(snapshot, strategy)
    if purpose is not None:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=purpose,
            price=candle.close,
        )
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    if paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price):
        return await _marketable_exit(
            snapshot,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.STOP,
            price=paper_stop_fill_price(
                side=position.side, candle=candle, stop_price=position.stop_price
            ),
            cooldown_bars=0,
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
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
    """Apply paper synthetic stops or live venue brackets after trailing.

    A pending flatten, a matched signal exit (ADR 0093), or a due time exit exits
    marketably at this bar's close (cancelling protection first) and never rests new
    protection. The signal exit precedes the time exit when both are due.
    """
    position = snapshot.position
    if position is None:
        return snapshot
    if flatten_requested(snapshot):
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.STOP,
            price=candle.close,
        )
    if position.signal_exit_bar is not None:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.SIGNAL_EXIT,
            price=candle.close,
        )
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
    """Rest one venue OCO bracket, replacing it when the working stop ratchets.

    Never rests new protection while a flatten is pending or a filled exit still awaits
    its fills, waits (without pausing) on an accepted cancel of the bracket it replaces,
    and holds back an identical bracket the venue already rejected (see exit_guards).
    """
    snapshot = await _adopt_venue_attached_child(
        snapshot, broker=broker, store=store, product_id=product.product_id
    )
    if detail := _exit_economics_fault(snapshot):
        return await _pause(snapshot, store=store, detail=detail)
    position = snapshot.position
    if position is None:
        return snapshot
    if _attached_entry_covers(snapshot, position):
        return await _mark_pending_exit(snapshot, store=store)
    cover = exit_order_side(position.side)
    existing = _active_side(snapshot.orders, cover)
    if existing is not None and _bracket_matches(existing, position):
        return await _mark_pending_exit(snapshot, store=store)
    if flatten_requested(snapshot) or exit_fill_pending(snapshot) is not None:
        return snapshot
    if existing is not None:
        snapshot = await _cancel_one_order(existing, broker=broker, store=store)
        remaining = _active_side(snapshot.orders, cover)
        if remaining is not None:
            if cancel_pending(remaining):
                return snapshot
            return await _pause(snapshot, store=store, detail=BRACKET_REPLACE_CANCEL_DETAIL)
        return await _replace_live_protection(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    return await _submit_live_bracket(
        snapshot, position=position, candle=candle, product=product, broker=broker, store=store
    )


async def _replace_live_protection(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Reconcile post-cancel executions before constructing replacement quantity/geometry."""
    # Cancel's confirmed GET may reveal execution absent from the initial view.
    # Only applied REST economics authorize a remaining protective quantity.
    snapshot = await _reconcile_stopped_live(snapshot, broker=broker, store=store)
    if detail := _exit_economics_fault(snapshot):
        return await _pause(snapshot, store=store, detail=detail)
    if snapshot.position is None:
        return snapshot
    return await _submit_live_bracket(
        snapshot,
        position=snapshot.position,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
    )


def _bracket_matches(order: Order, position: Position) -> bool:
    """Whether one resting order is the venue protection covering exactly this position.

    A book with a take-profit needs the TP/SL OCO; a book without one (ADR 0090) needs a
    stop-limit triggered at the working stop.
    """
    if order.stop_trigger_price != position.stop_price or order.quantity != position.quantity:
        return False
    if position.target_price is None:
        return order.kind is OrderKind.STOP_LIMIT
    return order.kind is OrderKind.TRIGGER_BRACKET and order.price == position.target_price


async def _submit_live_bracket(
    snapshot: DeploymentSnapshot,
    *,
    position: Position,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Submit one live protective order unless an identical rejection is still latched.

    With a take-profit this is the ``trigger_bracket_gtc`` OCO (limit = target). Without
    one (ADR 0090) it is a ``stop_limit_stop_limit_gtc`` triggered at the stop whose
    limit sits 5% through it, the same offset Coinbase applies to a bracket's stop leg.
    """
    rejection = bracket_rejection(snapshot, position)
    if rejection is not None and rejection_latched(snapshot, rejection, now=utc_now()):
        return await _pause_bracket_rejected(snapshot, store=store, position=position)
    cover = exit_order_side(position.side)
    target = position.target_price
    kind = OrderKind.TRIGGER_BRACKET
    price = target
    if target is None:
        kind = OrderKind.STOP_LIMIT
        price = protective_stop_limit_price(
            cover_side=cover,
            stop_price=position.stop_price,
            price_increment=product.price_increment,
        )
        if price <= 0:
            return await _pause(snapshot, store=store, detail=STOP_LIMIT_PRICE_DETAIL)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.BRACKET,
        side=cover,
        kind=kind,
        quantity=position.quantity,
        price=price,
        stop_trigger_price=position.stop_price,
        candle=candle,
    )
    current = await store.get_deployment(snapshot.deployment.id)
    if order.status is OrderStatus.OPEN:
        return await _mark_pending_exit(current, store=store)
    if order.status in {OrderStatus.UNKNOWN, OrderStatus.PENDING}:
        return await _pause(current, store=store, detail=BRACKET_UNCONFIRMED_DETAIL)
    if order.status is OrderStatus.REJECTED:
        return await _pause_bracket_rejected(current, store=store, position=position)
    return await _pause(current, store=store, detail=BRACKET_NOT_RESTED_DETAIL)


async def _pause_bracket_rejected(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot:
    """Pause once per venue rejection of a live bracket and audit it."""
    rejection = bracket_rejection(snapshot, position)
    if rejection is None:
        return await _pause(snapshot, store=store, detail=BRACKET_NOT_RESTED_DETAIL)
    return await _pause_rejected(snapshot, store=store, rejection=rejection, position=position)


async def _ensure_exit_protection(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    strategy: StrategyDefinition | None = None,
) -> DeploymentSnapshot:
    """Rest paper take-profit or a live venue bracket when already evaluated.

    With the strategy known, a book whose exit is already due (pending flatten, or a
    reached time exit still waiting on a venue cancel) keeps exiting instead of
    re-resting protection between bars.
    """
    purpose = _due_exit_purpose(snapshot, strategy)
    if strategy is not None and purpose is not None:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=purpose,
            price=candle.close,
        )
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


def _due_exit_purpose(
    snapshot: DeploymentSnapshot, strategy: StrategyDefinition | None
) -> IntentPurpose | None:
    """Return why a live open book must keep exiting between bars, or None to protect it.

    A pending flatten exits as STOP; a position marked by a matched ``exits.signal_exit``
    rule keeps exiting as SIGNAL_EXIT (ADR 0093); a reached ``max_bars_held`` keeps
    exiting as TIME_EXIT, so a cancel that completes between bars is followed by the exit
    rather than by a freshly rested bracket. Paper exits only on closed bars.
    """
    if snapshot.deployment.mode is not DeploymentMode.LIVE or snapshot.position is None:
        return None
    if flatten_requested(snapshot):
        return IntentPurpose.STOP
    if snapshot.position.signal_exit_bar is not None:
        return IntentPurpose.SIGNAL_EXIT
    if strategy is None:
        return None
    if snapshot.deployment.bars_held >= strategy.exits.time_exit.max_bars_held:
        return IntentPurpose.TIME_EXIT
    return None


async def _ensure_take_profit(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest a post-only take-profit when the position has none.

    A book without a target (``take_profit: {"kind": "none"}``) rests nothing: paper
    enforces its stop synthetically on closed bars and exits on the trail or time exit.
    """
    position = snapshot.position
    if position is None or position.target_price is None:
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
