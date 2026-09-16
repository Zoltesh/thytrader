"""Atomic, idempotent fill application shared by the loop, reconcile, and discretionary flows.

A fill's cash, inventory, and order-progress effects must apply exactly once, even
after a crash between steps or a retried submission/reconciliation call. Every
caller that turns one exact fill into a durable economic effect goes through
:func:`apply_fill`, which computes the effect as a pure value and hands it to the
store's single-transaction ``apply_fill_effect``. A fill already marked applied
(by an earlier call, before or after a crash) is a no-op here: the store detects it
and this module returns the current durable snapshot without repeating any side
effect. This is what keeps ``inventory == applied fills`` and
``cash == baseline + signed fill cashflows`` true across restarts.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import (
    DeploymentStatus,
    Fill,
    FillApplication,
    OrderSide,
    Position,
    PositionSide,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.execution.broker import Broker
    from thytrader.execution.models import DeploymentSnapshot, Order
    from thytrader.execution.store import ExecutionStore


async def apply_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Compute one fill's cash/position/order effect and persist it atomically.

    Dispatches to the entry, same-side add, or exit computation based on the
    current position, then applies the result through one atomic, idempotent
    store transaction. Returns the current durable snapshot whether or not this
    call was the one that newly applied the fill.
    """
    position = snapshot.position
    if position is None:
        application = _compute_entry_fill(snapshot, fill=fill, order=order)
    else:
        scaling_in = (position.side is PositionSide.LONG and order.side is OrderSide.BUY) or (
            position.side is PositionSide.SHORT and order.side is OrderSide.SELL
        )
        if scaling_in:
            application = _compute_scale_in_fill(
                snapshot, fill=fill, order=order, position=position
            )
        else:
            application = _compute_exit_fill(
                snapshot, fill=fill, order=order, cooldown_bars=cooldown_bars, position=position
            )
    await store.apply_fill_effect(application)
    return await store.get_deployment(order.deployment_id)


async def import_live_fills(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Fetch and atomically apply every unseen venue fill for one order.

    Used whenever a live acknowledgement alone is not sufficient evidence of an
    applied economic event (see ``apply_or_import_fill``): the venue's fill ledger,
    with its real quantities and fees, is the only source of truth for live orders.
    """
    known = {item.venue_fill_id for item in snapshot.fills if item.applied_at is not None}
    remote_fills = await broker.list_fills(product_id=product_id, order_id=order.venue_order_id)
    current = snapshot
    for remote in remote_fills:
        if remote.venue_fill_id in known:
            continue
        local = Fill(
            id=uuid7(utc_now()),
            deployment_id=order.deployment_id,
            order_id=order.id,
            venue_fill_id=remote.venue_fill_id,
            price=remote.price,
            quantity=remote.quantity,
            fee=remote.fee,
            filled_at=remote.filled_at,
        )
        known.add(local.venue_fill_id)
        current = await apply_fill(
            current, fill=local, order=order, store=store, cooldown_bars=cooldown_bars
        )
    return current


async def apply_or_import_fill(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    store: ExecutionStore,
    broker: Broker,
    product_id: str,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Apply a locally recorded (paper-simulated) fill, or import real fills for live.

    ``submit_intent`` only ever fabricates an immediate fill for the paper broker; a
    live acknowledgement is order progress only, never economic evidence on its own
    (F02). When no local fill exists for this order, this imports and applies the
    venue's real fills and fees instead of trusting the aggregate order quantity.
    """
    current = snapshot
    local = tuple(item for item in current.fills if item.order_id == order.id)
    if local:
        for fill in local:
            current = await apply_fill(
                current, fill=fill, order=order, store=store, cooldown_bars=cooldown_bars
            )
        return current
    return await import_live_fills(
        current,
        order=order,
        broker=broker,
        store=store,
        product_id=product_id,
        cooldown_bars=cooldown_bars,
    )


async def apply_unapplied_fills(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Apply every locally recorded fill that is not yet marked applied.

    Closes the paper ``save_fill``-then-crash window: a fill row can exist
    without its cash/inventory effect. The next closed-bar cycle or
    reconciliation must complete those rows instead of treating fill presence
    as proof of application (F01, F02).
    """
    current = snapshot
    orders = {item.id: item for item in current.orders}
    for fill in snapshot.fills:
        if fill.applied_at is not None:
            continue
        order = orders.get(fill.order_id)
        if order is None:
            continue
        current = await apply_fill(
            current, fill=fill, order=order, store=store, cooldown_bars=cooldown_bars
        )
        orders = {item.id: item for item in current.orders}
    return current


def _compute_entry_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
) -> FillApplication:
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
        return FillApplication(
            fill=fill, order=order, deployment=paused, position=None, clear_position=True
        )
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
        product_id=order.product_id or deployment.product_id,
        add_count=1,
        last_fill_intent_id=order.intent_id,
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
    return FillApplication(
        fill=fill, order=order, deployment=updated, position=position, clear_position=False
    )


def _compute_scale_in_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    position: Position,
) -> FillApplication:
    """Add to an existing position on the same side.

    Counts a new same-side add intent only when this fragment's order intent
    differs from the position's last applied intent (F28): one order that fills
    across many partial fragments consumes exactly one pyramiding slot, and a
    genuinely new add intent consumes exactly one more regardless of how many
    fragments it takes to fill.
    """
    deployment = snapshot.deployment
    now = utc_now()
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    quantity = position.quantity + fill.quantity
    entry_price = (
        (position.entry_price * position.quantity) + (fill.price * fill.quantity)
    ) / quantity
    same_intent = position.last_fill_intent_id == order.intent_id
    add_count = position.add_count if same_intent else position.add_count + 1
    updated_position = replace(
        position,
        quantity=quantity,
        entry_price=entry_price,
        updated_at=now,
        add_count=add_count,
        last_fill_intent_id=order.intent_id,
    )
    updated = with_runtime(
        deployment,
        updated_at=now,
        cash=cash,
        phase=RuntimePhase.OPEN if deployment.phase is RuntimePhase.FLAT else deployment.phase,
    )
    return FillApplication(
        fill=fill,
        order=order,
        deployment=updated,
        position=updated_position,
        clear_position=False,
    )


def _compute_exit_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    cooldown_bars: int,
    position: Position,
) -> FillApplication:
    """Reduce or flatten the open position after a covering fill."""
    deployment = snapshot.deployment
    now = utc_now()
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    if fill.quantity < position.quantity:
        remaining = replace(position, quantity=position.quantity - fill.quantity, updated_at=now)
        updated = with_runtime(deployment, updated_at=now, cash=cash)
        return FillApplication(
            fill=fill, order=order, deployment=updated, position=remaining, clear_position=False
        )
    updated = with_runtime(
        deployment,
        updated_at=now,
        cash=cash,
        phase=RuntimePhase.FLAT,
        bars_held=0,
        pending_entry_bars=0,
        cooldown_bars_remaining=max(cooldown_bars, 0),
        clear_pending_levels=True,
    )
    return FillApplication(
        fill=fill, order=order, deployment=updated, position=None, clear_position=True
    )


def _cash_after_fill(cash: Decimal, *, fill: Fill, order_side: OrderSide) -> Decimal:
    """Apply quote cash for a spot buy (debit) or sell (credit)."""
    notional = fill.price * fill.quantity
    if order_side is OrderSide.BUY:
        return cash - notional - fill.fee
    return cash + notional - fill.fee
