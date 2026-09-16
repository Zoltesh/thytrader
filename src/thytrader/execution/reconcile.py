"""Apply REST v3 fills onto locally persisted orders."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.fill_ledger import apply_fill, apply_unapplied_fills
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import DeploymentStatus, Fill, OrderStatus, with_runtime

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.execution.models import DeploymentSnapshot, Order
    from thytrader.execution.store import ExecutionStore

_WATCH = {OrderStatus.OPEN, OrderStatus.UNKNOWN, OrderStatus.PENDING, OrderStatus.FILLED}


async def reconcile_open_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """GET each watched order and ingest fills that are missing locally."""
    snapshot = await apply_unapplied_fills(snapshot, store=store, cooldown_bars=cooldown_bars)
    known = {fill.venue_fill_id for fill in snapshot.fills if fill.applied_at is not None}
    for order in tuple(snapshot.orders):
        snapshot = await _reconcile_one_order(
            snapshot,
            order=order,
            broker=broker,
            store=store,
            product_id=product_id,
            known=known,
            cooldown_bars=cooldown_bars,
        )
        if snapshot.deployment.status is DeploymentStatus.PAUSED:
            return snapshot
    return await store.get_deployment(snapshot.deployment.id)


async def _reconcile_one_order(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    known: set[str],
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Refresh one watched order from REST JSON and apply unseen fills."""
    if not _needs_reconcile(order, snapshot):
        return snapshot
    result = await broker.get_order(
        venue_order_id=order.venue_order_id or "",
        client_order_id=order.client_order_id,
    )
    venue_order_id = result.venue_order_id or order.venue_order_id
    if not venue_order_id:
        paused = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Order submit is unconfirmed and has no venue id.",
        )
        await store.save_deployment(paused)
        return await store.get_deployment(order.deployment_id)
    updated = replace(
        order,
        venue_order_id=venue_order_id,
        status=result.status,
        filled_quantity=result.filled_quantity,
        reject_reason=result.reject_reason,
        updated_at=utc_now(),
    )
    await store.save_order(updated)
    remote_fills = await broker.list_fills(product_id=product_id, order_id=updated.venue_order_id)
    if result.status is OrderStatus.FILLED and not remote_fills:
        paused = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Filled order has no REST fills.",
        )
        await store.save_deployment(paused)
        return await store.get_deployment(order.deployment_id)
    return await _ingest_fills(
        snapshot,
        order=updated,
        remote_fills=remote_fills,
        store=store,
        known=known,
        cooldown_bars=cooldown_bars,
    )


def _needs_reconcile(order: Order, snapshot: DeploymentSnapshot) -> bool:
    """Return whether local fill coverage is still incomplete for this order."""
    if order.status not in _WATCH:
        return False
    if order.status is not OrderStatus.FILLED:
        return True
    local = sum(
        (
            fill.quantity
            for fill in snapshot.fills
            if fill.order_id == order.id and fill.applied_at is not None
        ),
        start=Decimal("0"),
    )
    covered = order.filled_quantity if order.filled_quantity > 0 else order.quantity
    return local < covered


async def _ingest_fills(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    remote_fills: tuple[Fill, ...],
    store: ExecutionStore,
    known: set[str],
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Record and apply unseen venue fills atomically (F01): no separate save-then-apply step.

    ``apply_fill`` inserts each fill and applies its cash/position/order effect in
    one transaction, so a crash between "fill known" and "fill applied" cannot
    happen here, and a fill already applied by an earlier call is a safe no-op.
    """
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
            current,
            fill=local,
            order=order,
            store=store,
            cooldown_bars=cooldown_bars,
        )
    return current
