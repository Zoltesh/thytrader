"""Apply REST v3 fills onto locally persisted orders."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.loop import apply_fill
from thytrader.execution.models import DeploymentStatus, Fill, OrderStatus, with_runtime

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.execution.models import DeploymentSnapshot, Order
    from thytrader.execution.store import ExecutionStore


async def reconcile_open_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
) -> DeploymentSnapshot:
    """GET each open order and ingest fills that are missing locally."""
    known = {fill.venue_fill_id for fill in snapshot.fills}
    for order in tuple(snapshot.orders):
        snapshot = await _reconcile_one_order(
            snapshot,
            order=order,
            broker=broker,
            store=store,
            product_id=product_id,
            known=known,
        )
    return await store.get_deployment(snapshot.deployment.id)


async def _reconcile_one_order(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    known: set[str],
) -> DeploymentSnapshot:
    """Refresh one watched order from REST JSON and apply unseen fills."""
    watch = {OrderStatus.OPEN, OrderStatus.UNKNOWN, OrderStatus.PENDING}
    if order.status not in watch or order.venue_order_id is None:
        return snapshot
    result = await broker.get_order(
        venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
    )
    updated = replace(
        order,
        status=result.status,
        filled_quantity=result.filled_quantity,
        reject_reason=result.reject_reason,
        updated_at=utc_now(),
    )
    await store.save_order(updated)
    remote_fills = await broker.list_fills(product_id=product_id, order_id=order.venue_order_id)
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
        snapshot, order=updated, remote_fills=remote_fills, store=store, known=known
    )


async def _ingest_fills(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    remote_fills: tuple[Fill, ...],
    store: ExecutionStore,
    known: set[str],
) -> DeploymentSnapshot:
    """Persist unseen venue fills and update local cash/position."""
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
        await store.save_fill(local)
        known.add(local.venue_fill_id)
        current = await apply_fill(
            await store.get_deployment(order.deployment_id),
            fill=local,
            order=order,
            store=store,
        )
    return current
