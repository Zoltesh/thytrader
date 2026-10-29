"""Apply REST v3 fills onto locally persisted orders."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.broker import CANCEL_PENDING_REASON, BrokerError, ClientOrderLookup
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile_fill_order import apply_new_fills_in_execution_order
from thytrader.trading.adoption import ADOPTION_EVIDENCE_INCOMPLETE, adoption_evidence_complete
from thytrader.trading.fill_ledger import (
    applied_fill_quantity,
    creation_order,
    fill_economics_complete,
    ingest_fill,
    replay_unapplied_fills,
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.models import (
    TERMINAL_READ_UNCONFIRMED_REASON,
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
from thytrader.trading.overlay import InstrumentScopedStore, overlay_snapshot

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.broker import Broker, SubmitResult
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

UNCONFIRMED_SUBMIT_PREFIX = "Order submit is unconfirmed"
FILLED_WITHOUT_REST_FILLS_DETAIL = "Filled order has no REST fills."
RECONCILE_UNCONFIRMED_PREFIX = "Order reconciliation is unconfirmed"
ADOPTION_EVIDENCE_INCOMPLETE_DETAIL = (
    f"{ADOPTION_EVIDENCE_INCOMPLETE}: an inventory adoption lacks one applied fill "
    "covering its full quantity."
)

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
    if snapshot.deployment.status is DeploymentStatus.RUNNING and unprojected_inventory_products(
        snapshot
    ):
        snapshot = await _record_reconcile_fault(
            snapshot, store=store, detail="Applied fills contain unprojected inventory."
        )
    unread = snapshot
    snapshot, broker, applied = await apply_new_fills_in_execution_order(
        snapshot,
        broker=broker,
        store=store,
        product_id=product_id,
        cooldown_bars=cooldown_bars,
        watched=lambda order: _needs_reconcile(order, unread),
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
    # A paper broker cannot answer for a live order: such a walk reads nothing (None).
    walked = (
        None
        if isinstance(broker, PaperBroker) and snapshot.deployment.mode is not DeploymentMode.PAPER
        else frozenset(order.id for order in unread.orders)
    )
    # New fills were applied above in execution order. The walk still reads every order that
    # was watched before them (their GET refreshes status and adopts attached children),
    # parents before their attached children.
    for listed in sorted(unread.orders, key=creation_order):
        # An order whose fills were applied above is read from its current row, so the read
        # keeps that progress.
        order = next(
            (item for item in snapshot.orders if item.id == listed.id and item.id in applied),
            listed,
        )
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
            read=order.id in applied,
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
    snapshot = await _retain_reconcile_supervision(
        snapshot,
        store=store,
        started_status=started.status,
        started_detail=started.mismatch_detail,
        first_fault=first_fault,
    )
    if first_fault is None and _read_fault_confirmed(
        await store.get_accounting_snapshot(snapshot.deployment.id), walked=walked
    ):
        return await _clear_confirmed_read_fault(snapshot, store=store)
    return snapshot


READ_FAULT_DETAILS = frozenset(
    {
        f"{RECONCILE_UNCONFIRMED_PREFIX}: order read failed.",
        f"{RECONCILE_UNCONFIRMED_PREFIX}: fill read failed.",
    }
)
"""Faults that only say a venue read did not answer; a later complete walk can disprove them.

"Venue status is unknown" is not one: the venue did answer, and fill ingestion can still mark
that order filled, so a repeat of that fault would not leave the order ``UNKNOWN``.
"""


def _read_fault_confirmed(snapshot: DeploymentSnapshot, *, walked: frozenset[UUID] | None) -> bool:
    """Whether a latched read fault is contradicted by the whole book's evidence.

    ``snapshot`` is the accounting snapshot of every product, since the detail is shared and a
    multi-instrument walk may run through one product's view. ``walked`` is None when the walk
    could not reach the venue at all (a live book handed the paper broker): nothing is
    disproved then, even if every order is already terminal. Every order that still needs a
    venue read must have been visited by this walk (``walked``): a sibling product's order can
    leave ``UNKNOWN`` through a cancel acknowledgement without any successful read. A failed
    order or fill read marks its order ``UNKNOWN``, and a repeat of the same failure leaves the
    detail unchanged, so ``first_fault`` alone cannot prove the walk succeeded. Every order must
    therefore be out of ``UNKNOWN``: read back, or already terminal and accounted for. Every
    recorded execution must be covered by applied fills (a cancelled order can report an
    executed quantity whose fills were never read) and inventory must be projected, so
    clearing never hides a ledger fault.
    """
    if walked is None or snapshot.deployment.mismatch_detail not in READ_FAULT_DETAILS:
        return False
    if _unread_orders(snapshot, walked=walked) or unsettled_fill_evidence(snapshot):
        return False
    return not unprojected_inventory_products(snapshot)


def _unread_orders(snapshot: DeploymentSnapshot, *, walked: frozenset[UUID]) -> bool:
    """Whether any order in the book still lacks a successful read.

    That is an ``UNKNOWN`` order, one that still needs a venue read but was not visited by this
    walk, or an attached venue child named by a parent that has no local order row yet.
    """
    local = {order.venue_order_id for order in snapshot.orders if order.venue_order_id}
    return any(
        order.status is OrderStatus.UNKNOWN
        or (order.id not in walked and _needs_reconcile(order, snapshot))
        or (
            order.attached_child_venue_order_id is not None
            and order.attached_child_venue_order_id not in local
        )
        for order in snapshot.orders
    )


async def _clear_confirmed_read_fault(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Drop a disproved read fault; never resume, unpause or unstop the book.

    The book keeps its status: a paused book stays paused until an operator resumes it, so
    a transient venue outage can no longer leave a permanent "unconfirmed" detail behind,
    and it also cannot restart trading on its own.
    """
    deployment = snapshot.deployment
    detail = deployment.mismatch_detail
    await store.save_deployment(with_runtime(deployment, updated_at=utc_now(), clear_mismatch=True))
    await record_execution_audit(
        action="reconcile_read_fault_cleared",
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"deployment_id={deployment.id} status={deployment.status.value}: {detail!r} was "
            "cleared because every order now reads back from the venue or is terminal and "
            "accounted for. The status is unchanged; resuming stays manual."
        ),
        product_id=deployment.product_id,
    )
    return await store.get_deployment(deployment.id)


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
    read: bool = False,
) -> DeploymentSnapshot:
    """Refresh one watched order from REST JSON and apply unseen fills.

    ``read`` reads the order even when its fills already cover it (they were just applied).

    An adoption order never went to the venue (ADR 0124), so it is never looked up
    there. Its evidence is only checked locally, and incomplete evidence is a fault. An order
    with a venue id held by the paper broker is not read either: the store is its only source
    of truth, so only its local fill coverage is checked.
    """
    if order.kind is OrderKind.ADOPTION:
        return await _check_adoption_evidence(snapshot, order=order, store=store)
    if isinstance(broker, PaperBroker) and order.venue_order_id:
        # The paper broker answers every lookup with OPEN. A paper order's state lives only in
        # the store (a read would revive a canceled paper exit), and a live order reached with
        # the paper broker (no live adapter bound) must never take its fabricated answer.
        if snapshot.deployment.mode is not DeploymentMode.PAPER:
            return snapshot
        return await _check_local_fill_coverage(snapshot, order=order, store=store)
    return await _reconcile_venue_order(
        snapshot,
        order=order,
        broker=broker,
        store=store,
        product_id=product_id,
        known=known,
        cooldown_bars=cooldown_bars,
        read=read,
    )


async def _check_local_fill_coverage(
    snapshot: DeploymentSnapshot, *, order: Order, store: ExecutionStore
) -> DeploymentSnapshot:
    """Fault a paper order whose recorded execution has no applied fill to back it.

    A paper fill is saved after its order in a separate write, so an interrupted submit can
    leave a filled order without its fill. The paper broker cannot be asked, so the local
    ledger is checked instead, as a venue read's coverage check would.
    """
    if applied_fill_quantity(snapshot, order.id) < order.filled_quantity:
        return await _record_reconcile_fault(
            snapshot, store=store, detail=FILLED_WITHOUT_REST_FILLS_DETAIL
        )
    return snapshot


async def _check_adoption_evidence(
    snapshot: DeploymentSnapshot, *, order: Order, store: ExecutionStore
) -> DeploymentSnapshot:
    """Keep a fully evidenced adoption; fault one whose applied fill is missing or partial."""
    if adoption_evidence_complete(snapshot, order):
        return snapshot
    return await _record_reconcile_fault(
        snapshot, store=store, detail=ADOPTION_EVIDENCE_INCOMPLETE_DETAIL
    )


async def _reconcile_venue_order(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    known: set[str],
    cooldown_bars: int,
    read: bool = False,
) -> DeploymentSnapshot:
    """Read one watched venue order and apply its unseen fills."""
    if not read and not _needs_reconcile(order, snapshot):
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
        await _mark_read_failed(store, order)
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
        filled_quantity=max(order.filled_quantity, result.filled_quantity),
        reject_reason=(
            CANCEL_PENDING_REASON
            if order.reject_reason == CANCEL_PENDING_REASON
            and result.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
            else result.reject_reason
        ),
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
        await _mark_read_failed(store, updated)
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
    if applied_fill_quantity(current, updated.id) < updated.filled_quantity:
        return await _record_reconcile_fault(
            current, store=store, detail=FILLED_WITHOUT_REST_FILLS_DETAIL
        )
    if result.status is OrderStatus.UNKNOWN:
        return await _record_reconcile_fault(
            current, store=store, detail=f"{RECONCILE_UNCONFIRMED_PREFIX}: venue status is unknown."
        )
    return await _settle_canceled(current, order=updated, store=store)


CANCELED_SETTLE_DELAY = timedelta(minutes=15)
"""How long after its cancel a canceled order keeps being read before it may settle."""


async def _settle_canceled(
    current: DeploymentSnapshot, *, order: Order, store: ExecutionStore
) -> DeploymentSnapshot:
    """End venue reads of a canceled order once a consistent terminal read is old enough.

    Called after a successful read whose fills were applied: the venue shows the order
    canceled, and the applied REST fills already cover every executed quantity it reported.
    Once that holds at least ``CANCELED_SETTLE_DELAY`` after the order's first cancel time,
    the order is settled and leaves the watch set (ADR 0141). Until then it is read every
    cycle; a failed read never reaches here. The settlement is written onto the order as the
    store now holds it, so fill progress applied by this very read is never overwritten. Time
    is measured at this read (``order.updated_at``) rather than a new clock reading, so a
    replay tape captured before ADR 0141 holds every clock input settlement consumes.
    """
    if order.status is not OrderStatus.CANCELED or order.settled_at is not None:
        return current
    now = order.updated_at
    # ``order`` keeps the stored cancel time; none yet means it was canceled by this read.
    if order.canceled_at is None or now - order.canceled_at < CANCELED_SETTLE_DELAY:
        return current
    fresh = await store.get_deployment(current.deployment.id)
    stored = next((item for item in fresh.orders if item.id == order.id), None)
    if stored is None or stored.status is not OrderStatus.CANCELED or stored.canceled_at is None:
        return fresh
    if now - stored.canceled_at < CANCELED_SETTLE_DELAY:
        return fresh
    reported = max(stored.filled_quantity, order.filled_quantity)
    if applied_fill_quantity(fresh, order.id) < reported:
        return fresh
    await store.save_order(replace(stored, settled_at=now))
    return await store.get_deployment(current.deployment.id)


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


async def _mark_read_failed(store: ExecutionStore, order: Order) -> None:
    """Record a failed venue read without inventing a working order.

    Every order becomes ``UNKNOWN`` until a read succeeds: unknown late fills must keep
    exits, flatten settlement and exposure fail-closed. When the venue had already reported it
    terminal (canceled, or filled for its full quantity) the order is marked
    ``TERMINAL_READ_UNCONFIRMED_REASON`` so supervision never mistakes a historical stop for a
    triggered-but-unfilled live one. A ``FILLED`` order short of its quantity may be locally
    inferred while its remainder still works, so it is not marked.
    """
    reason = (
        TERMINAL_READ_UNCONFIRMED_REASON
        if _terminal(order) or order.reject_reason == TERMINAL_READ_UNCONFIRMED_REASON
        else order.reject_reason
    )
    await store.save_order(
        replace(
            order,
            status=OrderStatus.UNKNOWN,
            reject_reason=reason,
            venue_observed_at=None,
            updated_at=utc_now(),
        )
    )


def _terminal(order: Order) -> bool:
    """A venue-cancelled order, or one filled for its full quantity."""
    if order.status is OrderStatus.CANCELED:
        return True
    return order.status is OrderStatus.FILLED and order.filled_quantity >= order.quantity


def _needs_reconcile(order: Order, snapshot: DeploymentSnapshot) -> bool:
    """Return whether local fill coverage is still incomplete for this order.

    Never for an adoption: it has no venue order to read (ADR 0124).
    """
    if order.kind is OrderKind.ADOPTION or order.status not in _WATCH:
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
    # late fills even when the currently reported partial fills already applied,
    # until a consistent terminal read settles the order (ADR 0141).
    if order.status is OrderStatus.CANCELED:
        return order.settled_at is None
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
