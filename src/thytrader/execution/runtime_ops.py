"""Shared runtime operations of the closed-bar execution loop.

Pausing, runtime persistence, active entry/side lookup, resting-order matching and
cancellation, fill application, and venue reconciliation helpers. Entry, exit,
protection, residual and breaker modules build on these, as do discretionary and
stopped books; this module imports no other loop module.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.attached import filled_attached_entry
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.broker import CANCEL_PENDING_REASON, BrokerError
from thytrader.execution.exit_guards import (
    CANCEL_BEFORE_EXIT_DETAIL,
    SubmitRejection,
    active_orders,
    cancel_pending,
    rejection_detail,
)
from thytrader.execution.reconcile import import_attached_children, reconcile_open_orders
from thytrader.trading.fill_ledger import ingest_fill, unprojected_inventory_products
from thytrader.trading.geometry import paper_stop_hit
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
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
    RuntimePhase,
    is_venue_protection,
    resolved_product_id,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle
    from thytrader.trading.store import ExecutionStore


_ACTIVE = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}


async def _reconcile_stopped_live(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Apply live fills before a flatten decision; paper books stay local."""
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        return snapshot
    return await reconcile_open_orders(
        snapshot,
        broker=broker,
        store=store,
        product_id=snapshot.position.product_id if snapshot.position is not None else None,
        cooldown_bars=0,
    )


async def cancel_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel every locally open order, retaining ambiguous stopped remainders."""
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    current = await store.get_deployment(snapshot.deployment.id)
    blocking = active_orders(current)
    if current.deployment.status is DeploymentStatus.STOPPED and blocking:
        if all(cancel_pending(order) for order in blocking):
            return current
        return await _pause(current, store=store, detail=CANCEL_BEFORE_EXIT_DETAIL)
    return current


async def _match_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
    cooldown_bars: int,
    timeframe: str | None = None,
    stop_first: bool = False,
) -> DeploymentSnapshot:
    """Apply paper or local fills for resting limits against the closed candle.

    With ``stop_first`` (paper), a resting take-profit is not matched on a candle that also
    trades through the position's stop: the candle cannot show which traded first, so the
    stop exit in position management wins (the same rule as the backtest model, ADR 0083).
    """
    for order in snapshot.orders:
        if order.status is not OrderStatus.OPEN:
            continue
        if stop_first and _stop_preempts_take_profit(snapshot, order=order, candle=candle):
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


def _stop_preempts_take_profit(
    snapshot: DeploymentSnapshot, *, order: Order, candle: Candle
) -> bool:
    """Return whether this resting take-profit must yield to a stop the candle also hit."""
    intent = next((item for item in snapshot.intents if item.id == order.intent_id), None)
    if intent is None or intent.purpose is not IntentPurpose.TAKE_PROFIT:
        return False
    position = snapshot.position
    if position is None:
        return False
    if order.product_id and position.product_id and order.product_id != position.product_id:
        return False
    return paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price)


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


async def _adopt_venue_attached_child(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
) -> DeploymentSnapshot:
    """Learn an attached TP/SL child the venue reports for an already-filled entry.

    Coinbase may report ``attached_order_id`` only on GET order. Without it the open
    position looks unprotected and a second bracket would be rejected because the base
    is already on hold by the attached child. Ask the venue once before resting anything.
    """
    position = snapshot.position
    if position is None:
        return snapshot
    entry = filled_attached_entry(snapshot, position)
    if entry is None or entry.attached_child_venue_order_id or not entry.venue_order_id:
        return snapshot
    try:
        observed = await broker.get_order(
            venue_order_id=entry.venue_order_id, client_order_id=entry.client_order_id
        )
    except BrokerError:
        return snapshot
    child_id = observed.attached_child_venue_order_id
    if not child_id:
        return snapshot
    await store.save_order(
        replace(entry, attached_child_venue_order_id=child_id, updated_at=utc_now())
    )
    refreshed = await store.get_deployment(snapshot.deployment.id)
    return await import_attached_children(
        refreshed, broker=broker, store=store, product_id=product_id
    )


async def _pause_rejected(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    rejection: SubmitRejection,
    position: Position,
) -> DeploymentSnapshot:
    """Pause once per deterministic protective rejection and audit it; latched cycles no-op."""
    detail = rejection_detail(rejection)
    deployment = snapshot.deployment
    if deployment.mismatch_detail == detail and deployment.status is not DeploymentStatus.RUNNING:
        return snapshot
    paused = await _pause(snapshot, store=store, detail=detail)
    await record_execution_audit(
        action="protective_submit_latched",
        outcome=AuditEventOutcome.FAILURE,
        detail=(
            f"deployment_id={deployment.id} rejections={rejection.count} "
            f"retry_after={rejection.retry_at().isoformat()} reason={rejection.reason[:300]}: "
            f"identical {rejection.label} re-submits are held back with exponential backoff."
        ),
        product_id=position.product_id or deployment.product_id,
    )
    return paused


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


async def _cancel_open_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> None:
    """Cancel every active order the venue can identify before a marketable exit."""
    for order in snapshot.orders:
        if order.status in _ACTIVE and order.venue_order_id:
            await _cancel_one_order(order, broker=broker, store=store)


async def _cancel_one_order(
    order: Order, *, broker: Broker, store: ExecutionStore
) -> DeploymentSnapshot:
    """Cancel one active order when the venue id is known, confirming via GET order.

    An order whose cancel the venue already accepted is only re-checked with GET order,
    never re-cancelled, so a slow venue cancel cannot turn into a retry storm. A transport
    failure leaves the order unchanged for the next cycle.
    """
    snapshot = await store.get_deployment(order.deployment_id)
    if order.venue_order_id is None:
        return snapshot
    if resolved_product_id(order.product_id, snapshot.deployment) in unprojected_inventory_products(
        snapshot
    ) and (
        is_venue_protection(order.kind)
        or not any(
            intent.id == order.intent_id and intent.purpose is IntentPurpose.ENTRY
            for intent in snapshot.intents
        )
    ):
        # An absent/incomplete position is not authority to remove real protection.
        return snapshot
    try:
        if order.reject_reason == CANCEL_PENDING_REASON:
            result = await broker.get_order(
                venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
            )
            if result.status in _ACTIVE:
                result = replace(result, reject_reason=CANCEL_PENDING_REASON)
        else:
            result = await broker.cancel_order(
                venue_order_id=order.venue_order_id,
                client_order_id=order.client_order_id,
            )
    except BrokerError:
        return await store.get_deployment(order.deployment_id)
    await store.save_order(
        replace(
            order,
            status=result.status,
            filled_quantity=max(order.filled_quantity, result.filled_quantity),
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
    """Pause when venue state cannot be reconciled safely.

    A STOPPED book (managed shutdown or flatten in progress) keeps its STOPPED status and
    only records the detail: re-pausing it would hand a requested stop back to the
    running/paused protection path, which may rest new brackets.
    """
    status = DeploymentStatus.PAUSED
    if snapshot.deployment.status is DeploymentStatus.STOPPED:
        status = DeploymentStatus.STOPPED
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=status,
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
    """Return the working entry order, preferring purpose-tagged intents.

    Without any ENTRY intent the fallback considers only orders whose intent is unknown,
    so a working exit on a book opened by adoption (ADR 0124) is never managed as an
    entry.
    """
    known = {intent.id for intent in snapshot.intents}
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
            and order.intent_id not in known
        ),
        None,
    )
