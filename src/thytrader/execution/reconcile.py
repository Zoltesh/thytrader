"""Apply REST v3 fills onto locally persisted orders."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.broker import BrokerError, ClientOrderLookup
from thytrader.execution.fill_ledger import (
    applied_fill_quantity,
    fill_economics_complete,
    ingest_fill,
    replay_unapplied_fills,
)
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    with_runtime,
)
from thytrader.execution.overlay import InstrumentScopedStore, overlay_snapshot
from thytrader.persistence.audit_events import AuditEventOutcome

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker, SubmitResult
    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore

UNCONFIRMED_SUBMIT_PREFIX = "Order submit is unconfirmed"
FILLED_WITHOUT_REST_FILLS_DETAIL = "Filled order has no REST fills."
RECONCILE_UNCONFIRMED_PREFIX = "Order reconciliation is unconfirmed"

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
    """GET each watched order and ingest fills that are missing locally.

    A preexisting operator pause does not end the walk. Every watched order and
    attached child is still reconciled. A new fault is retained, and neither a
    pause nor a stopped shutdown is cleared.
    """
    started = snapshot.deployment
    snapshot = await replay_unapplied_fills(snapshot, store=store, cooldown_bars=cooldown_bars)
    if any(fill.economics_applied_at is None for fill in snapshot.fills):
        snapshot = await _record_reconcile_fault(
            snapshot, store=store, detail="Stored fills have unapplied economics."
        )
    known = {fill.venue_fill_id for fill in snapshot.fills}
    first_fault = _first_new_fault(None, before=started, after=snapshot.deployment)
    snapshot = await _retain_reconcile_supervision(
        snapshot,
        store=store,
        started_status=started.status,
        started_detail=started.mismatch_detail,
        first_fault=first_fault,
    )
    for order in tuple(snapshot.orders):
        before = snapshot.deployment
        order_product = order.product_id or product_id or snapshot.deployment.product_id
        scoped = overlay_snapshot(snapshot, order_product)
        scoped = await _reconcile_one_order(
            scoped,
            order=order,
            broker=broker,
            store=InstrumentScopedStore(store, order_product),
            product_id=order_product,
            known=known,
            cooldown_bars=cooldown_bars,
        )
        snapshot = await store.get_deployment(snapshot.deployment.id)
        first_fault = _first_new_fault(first_fault, before=before, after=snapshot.deployment)
        snapshot = await _retain_reconcile_supervision(
            snapshot,
            store=store,
            started_status=started.status,
            started_detail=started.mismatch_detail,
            first_fault=first_fault,
        )
    # Re-read so attached child ids adopted while reconciling entries this cycle are seen.
    snapshot = await store.get_deployment(snapshot.deployment.id)
    before_children = snapshot.deployment
    snapshot = await _import_attached_children(
        snapshot, broker=broker, store=store, product_id=product_id, cooldown_bars=cooldown_bars
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    first_fault = _first_new_fault(first_fault, before=before_children, after=snapshot.deployment)
    return await _retain_reconcile_supervision(
        snapshot,
        store=store,
        started_status=started.status,
        started_detail=started.mismatch_detail,
        first_fault=first_fault,
    )


def _fault_status(deployment: Deployment) -> DeploymentStatus:
    """Keep a stopped shutdown stopped; every other book pauses on a new fault."""
    if deployment.status is DeploymentStatus.STOPPED:
        return DeploymentStatus.STOPPED
    return DeploymentStatus.PAUSED


def _first_new_fault(
    current: str | None,
    *,
    before: Deployment,
    after: Deployment,
) -> str | None:
    """Keep the first fault detail introduced while reconciling this cycle."""
    if current is not None:
        return current
    detail = after.mismatch_detail
    if detail is None or detail == before.mismatch_detail:
        return None
    if after.status not in {DeploymentStatus.PAUSED, DeploymentStatus.STOPPED}:
        return None
    return detail


async def _record_reconcile_fault(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    detail: str,
) -> DeploymentSnapshot:
    """Persist one reconcile fault without unpausing or unstopping the book."""
    faulted = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=_fault_status(snapshot.deployment),
        mismatch_detail=detail,
    )
    await store.save_deployment(faulted)
    return await store.get_deployment(snapshot.deployment.id)


async def _retain_reconcile_supervision(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    started_status: DeploymentStatus,
    started_detail: str | None,
    first_fault: str | None,
) -> DeploymentSnapshot:
    """Restore a preexisting pause or stop, keeping the first new fault detail."""
    deployment = snapshot.deployment
    status = _retained_status(started_status, deployment.status, first_fault)
    detail = first_fault if first_fault is not None else started_detail
    if status is deployment.status and detail == deployment.mismatch_detail:
        return snapshot
    if started_status is DeploymentStatus.RUNNING and first_fault is None:
        return snapshot
    retained = with_runtime(
        deployment,
        updated_at=utc_now(),
        status=status,
        mismatch_detail=detail,
        clear_mismatch=detail is None,
    )
    await store.save_deployment(retained)
    return await store.get_deployment(deployment.id)


def _retained_status(
    started: DeploymentStatus,
    current: DeploymentStatus,
    first_fault: str | None,
) -> DeploymentStatus:
    """Never unpause or unstop; a new fault pauses a book that was running."""
    if started is DeploymentStatus.STOPPED:
        return DeploymentStatus.STOPPED
    if started is DeploymentStatus.PAUSED:
        return DeploymentStatus.PAUSED
    if first_fault is not None:
        return DeploymentStatus.PAUSED
    return current


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
    try:
        if not order.venue_order_id and isinstance(broker, ClientOrderLookup):
            recovered = await _recover_unconfirmed_submit(
                order, broker=broker, product_id=product_id
            )
            if recovered is None:
                return await _pause_unconfirmed_submit(snapshot, order=order, store=store)
            result = recovered
        else:
            result = await broker.get_order(
                venue_order_id=order.venue_order_id or "",
                client_order_id=order.client_order_id,
            )
    except BrokerError:
        await store.save_order(
            replace(order, status=OrderStatus.UNKNOWN, venue_observed_at=None, updated_at=utc_now())
        )
        return await _record_reconcile_fault(
            snapshot, store=store, detail=f"{RECONCILE_UNCONFIRMED_PREFIX}: order read failed."
        )
    venue_order_id = result.venue_order_id or order.venue_order_id
    if not venue_order_id:
        return await _record_reconcile_fault(
            snapshot,
            store=store,
            detail=f"{UNCONFIRMED_SUBMIT_PREFIX} and has no venue id.",
        )
    updated = replace(
        order,
        venue_order_id=venue_order_id,
        status=result.status,
        filled_quantity=result.filled_quantity,
        reject_reason=result.reject_reason,
        venue_observed_at=(
            utc_now()
            if snapshot.deployment.mode is DeploymentMode.LIVE
            and result.status is not OrderStatus.UNKNOWN
            else None
        ),
        # Keep a known attached child; otherwise adopt the one the venue now reports, so an
        # entry whose create response omitted it is still recognized as venue-protected.
        attached_child_venue_order_id=(
            order.attached_child_venue_order_id or result.attached_child_venue_order_id
        ),
        updated_at=utc_now(),
    )
    await store.save_order(updated)
    try:
        remote_fills = await broker.list_fills(
            product_id=product_id, order_id=updated.venue_order_id
        )
    except BrokerError:
        await store.save_order(
            replace(
                updated, status=OrderStatus.UNKNOWN, venue_observed_at=None, updated_at=utc_now()
            )
        )
        return await _record_reconcile_fault(
            snapshot, store=store, detail=f"{RECONCILE_UNCONFIRMED_PREFIX}: fill read failed."
        )
    if result.status is OrderStatus.FILLED and not remote_fills:
        return await _record_reconcile_fault(
            snapshot, store=store, detail=FILLED_WITHOUT_REST_FILLS_DETAIL
        )
    current = await _ingest_fills(
        snapshot,
        order=updated,
        remote_fills=remote_fills,
        store=store,
        known=known,
        cooldown_bars=cooldown_bars,
    )
    if result.status is OrderStatus.UNKNOWN:
        return await _record_reconcile_fault(
            current, store=store, detail=f"{RECONCILE_UNCONFIRMED_PREFIX}: venue status is unknown."
        )
    return current


async def _recover_unconfirmed_submit(
    order: Order,
    *,
    broker: ClientOrderLookup,
    product_id: str,
) -> SubmitResult | None:
    """Resolve an ambiguous create by client_order_id; never re-submits.

    Returns the venue snapshot when Coinbase shows the order, or None when a
    complete bounded scan found nothing. The order then stays UNKNOWN.
    """
    found = await broker.find_order_by_client_id(
        client_order_id=order.client_order_id,
        product_id=product_id,
        submitted_at=order.created_at,
    )
    if found is None or not found.venue_order_id:
        return None
    await record_execution_audit(
        action="unconfirmed_order_recovered",
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"deployment_id={order.deployment_id} client_order_id={order.client_order_id} "
            f"venue_order_id={found.venue_order_id} status={found.status.value}: "
            "ambiguous submit located at Coinbase by client_order_id."
        ),
        product_id=product_id,
    )
    return found


async def _pause_unconfirmed_submit(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Keep an unlocated ambiguous submit UNKNOWN and pause fail-closed.

    Audits once per pause so the worker's per-cycle re-check does not flood the trail.
    """
    detail = (
        f"{UNCONFIRMED_SUBMIT_PREFIX}: no Coinbase order with client_order_id "
        f"{order.client_order_id} was found; verify on Coinbase before resuming."
    )
    first_observation = snapshot.deployment.mismatch_detail != detail
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=_fault_status(snapshot.deployment),
        mismatch_detail=detail,
    )
    await store.save_deployment(paused)
    if first_observation:
        await record_execution_audit(
            action="unconfirmed_order_not_found",
            outcome=AuditEventOutcome.FAILURE,
            detail=f"deployment_id={order.deployment_id} {detail}",
            product_id=order.product_id,
        )
    return await store.get_deployment(order.deployment_id)


def _needs_reconcile(order: Order, snapshot: DeploymentSnapshot) -> bool:
    """Return whether local fill coverage is still incomplete for this order."""
    if order.status not in _WATCH:
        return False
    if order.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}:
        return True
    # Atomic fill application may mark a partially executed order FILLED before
    # REST reveals its remaining order state. Applied fragments are not a watermark.
    if (
        order.status is OrderStatus.FILLED
        and applied_fill_quantity(snapshot, order.id) < order.quantity
    ):
        return True
    # A cancel acknowledgement is not a fill-visibility watermark. Keep learning
    # late fills even when the currently reported partial fills already applied.
    if order.status is OrderStatus.CANCELED:
        return True
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


async def ingest_order_fills(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """List one order's venue fills and apply the unseen ones; never pauses.

    Used right after a live marketable exit reports FILLED: Coinbase fills can lag the
    order status, so an empty or failed listing leaves the order for the next cycle.
    """
    if not order.venue_order_id:
        return snapshot
    try:
        remote_fills = await broker.list_fills(product_id=product_id, order_id=order.venue_order_id)
    except BrokerError:
        return snapshot
    known = {fill.venue_fill_id for fill in snapshot.fills}
    return await _ingest_fills(
        snapshot,
        order=order,
        remote_fills=remote_fills,
        store=store,
        known=known,
        cooldown_bars=cooldown_bars,
    )


async def import_attached_children(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str | None,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Public entry for importing venue attached children not yet tracked locally."""
    return await _import_attached_children(
        snapshot, broker=broker, store=store, product_id=product_id, cooldown_bars=cooldown_bars
    )


async def _import_attached_children(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str | None,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Persist and reconcile attached child venue orders that are not local yet."""
    known_venues = {order.venue_order_id for order in snapshot.orders if order.venue_order_id}
    current = snapshot
    first_fault: str | None = None
    for order in tuple(snapshot.orders):
        child_id = order.attached_child_venue_order_id
        if not child_id or child_id in known_venues:
            continue
        # Persist the known child identity before a venue read: a failed import
        # must remain locally blocking rather than disappear from supervision.
        before = current.deployment
        child = Order(
            id=uuid7(utc_now()),
            deployment_id=order.deployment_id,
            intent_id=order.intent_id,
            client_order_id=f"{order.client_order_id}:child"[:128],
            side=OrderSide.SELL if order.side is OrderSide.BUY else OrderSide.BUY,
            kind=OrderKind.TRIGGER_BRACKET,
            quantity=order.quantity,
            price=order.take_profit_price,
            stop_trigger_price=order.stop_trigger_price,
            take_profit_price=order.take_profit_price,
            status=OrderStatus.UNKNOWN,
            created_at=utc_now(),
            updated_at=utc_now(),
            venue_order_id=child_id,
            product_id=order.product_id or product_id or snapshot.deployment.product_id,
            parent_order_id=order.id,
        )
        await store.save_order(child)
        known_venues.add(child_id)
        current = await store.get_deployment(order.deployment_id)
        order_product = child.product_id or product_id or snapshot.deployment.product_id
        await _reconcile_one_order(
            overlay_snapshot(current, order_product),
            order=child,
            broker=broker,
            store=InstrumentScopedStore(store, order_product),
            product_id=order_product,
            known={fill.venue_fill_id for fill in current.fills},
            cooldown_bars=cooldown_bars,
        )
        current = await store.get_deployment(order.deployment_id)
        first_fault = _first_new_fault(first_fault, before=before, after=current.deployment)
    return await _retain_reconcile_supervision(
        current,
        store=store,
        started_status=snapshot.deployment.status,
        started_detail=snapshot.deployment.mismatch_detail,
        first_fault=first_fault,
    )
