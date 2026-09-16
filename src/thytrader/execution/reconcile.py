"""Apply REST v3 fills onto locally persisted orders."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.fill_ledger import (
    applied_fill_quantity,
    fill_economics_complete,
    ingest_fill,
    replay_unapplied_fills,
)
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import DeploymentStatus, Fill, OrderStatus, with_runtime
from thytrader.execution.overlay import overlay_snapshot

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.execution.models import DeploymentSnapshot, Order
    from thytrader.execution.store import ExecutionStore

_WATCH = {
    OrderStatus.OPEN,
    OrderStatus.UNKNOWN,
    OrderStatus.PENDING,
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
}


async def reconcile_open_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str | None = None,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """GET each watched order and ingest fills that are missing locally."""
    snapshot = await replay_unapplied_fills(snapshot, store=store, cooldown_bars=cooldown_bars)
    known = {fill.venue_fill_id for fill in snapshot.fills}
    for order in tuple(snapshot.orders):
        order_product = order.product_id or product_id or snapshot.deployment.product_id
        scoped = overlay_snapshot(snapshot, order_product)
        scoped = await _reconcile_one_order(
            scoped,
            order=order,
            broker=broker,
            store=store,
            product_id=order_product,
            known=known,
            cooldown_bars=cooldown_bars,
        )
        snapshot = _merge_overlay(snapshot, scoped, order_product)
        if snapshot.deployment.status is DeploymentStatus.PAUSED:
            return snapshot
    return await store.get_deployment(snapshot.deployment.id)


def _merge_overlay(
    parent: DeploymentSnapshot,
    scoped: DeploymentSnapshot,
    product_id: str,
) -> DeploymentSnapshot:
    """Merge one product overlay back into the parent multi-book snapshot."""
    del product_id
    return replace(
        parent,
        deployment=scoped.deployment,
        position=scoped.position if scoped.position is not None else parent.position,
        positions=scoped.positions or parent.positions,
        instrument_runtimes=scoped.instrument_runtimes or parent.instrument_runtimes,
    )


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
    if fill_economics_complete(snapshot, order):
        return False
    if order.status is not OrderStatus.FILLED:
        return True
    covered = order.filled_quantity if order.filled_quantity > 0 else order.quantity
    applied = applied_fill_quantity(snapshot, order.id)
    return applied < covered


async def _ingest_fills(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    remote_fills: tuple[Fill, ...],
    store: ExecutionStore,
    known: set[str],
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Persist unseen venue fills and update local cash/position atomically."""
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
            venue_order_id=remote.venue_order_id,
        )
        known.add(local.venue_fill_id)
        result = await ingest_fill(
            current,
            fill=local,
            order=order,
            store=store,
            cooldown_bars=cooldown_bars,
        )
        current = result.snapshot
    return current
