"""Idempotent fill evidence and economic projection for paper and live books."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from thytrader.execution.geometry import entry_bar_bucket
from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    Deployment,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    aggregate_phase,
    is_venue_protection,
    resolved_product_id,
    runtime_from_deployment,
    snapshot_positions,
    with_runtime,
)
from thytrader.execution.overlay import overlay_snapshot

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.store import ExecutionStore


def _cash_after_fill(cash: Decimal, *, fill: Fill, order_side: OrderSide) -> Decimal:
    """Apply quote cash for a spot buy (debit) or sell (credit)."""
    notional = fill.price * fill.quantity
    if order_side is OrderSide.BUY:
        return cash - notional - fill.fee
    return cash + notional - fill.fee


@dataclass(frozen=True, slots=True)
class FillIngestResult:
    """Outcome of one fill-ingest attempt."""

    applied: bool
    snapshot: DeploymentSnapshot


def applied_fill_quantity(snapshot: DeploymentSnapshot, order_id: UUID) -> Decimal:
    """Sum quantity from fills whose economics were applied for one order."""
    return sum(
        (
            fill.quantity
            for fill in snapshot.fills
            if fill.order_id == order_id and fill.economics_applied_at is not None
        ),
        start=Decimal("0"),
    )


def fill_economics_complete(snapshot: DeploymentSnapshot, order: Order) -> bool:
    """Return whether every recorded fill for this order has been economically applied."""
    order_fills = tuple(item for item in snapshot.fills if item.order_id == order.id)
    if not order_fills:
        return False
    if not all(item.economics_applied_at is not None for item in order_fills):
        return False
    covered = order.filled_quantity if order.filled_quantity > 0 else order.quantity
    applied = applied_fill_quantity(snapshot, order.id)
    return applied >= covered


def unsettled_fill_evidence(snapshot: DeploymentSnapshot) -> bool:
    """Block further execution while recorded executions exceed applied economics.

    A terminal cancel only settles its remainder, not its executed quantity. Applied
    fragments do not prove a locally FILLED order is complete either.
    """
    applied: dict[UUID, Decimal] = {}
    for fill in snapshot.fills:
        if fill.economics_applied_at is None:
            return True
        applied[fill.order_id] = applied.get(fill.order_id, Decimal(0)) + fill.quantity
    return any(
        applied.get(order.id, Decimal(0))
        < (
            max(order.quantity, order.filled_quantity)
            if order.status is OrderStatus.FILLED
            else order.filled_quantity
        )
        for order in snapshot.orders
    )


def unprojected_inventory_products(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """Identify owned applied inventory not represented by the position projection.

    Retained orders/fills, not a mutable mismatch string, are the durable evidence.
    Only products with applied entry evidence are anchored; legacy seeded positions
    without an entry ledger are not reconstructed or assigned invented geometry.
    A larger same-side legacy position is allowed, but cannot hide recorded inventory
    on the opposite side or a recorded balance larger than the projected position.
    This predicate never supplies an executable quantity or repairs historical fills.
    """
    entries = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    orders = {order.id: order for order in snapshot.orders}
    anchors: dict[str, datetime] = {}
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if (
            fill.economics_applied_at is None
            or order is None
            or order.intent_id not in entries
            or is_venue_protection(order.kind)
        ):
            continue
        product_id = resolved_product_id(order.product_id, snapshot.deployment)
        anchors[product_id] = min(anchors.get(product_id, order.created_at), order.created_at)
    balances = _anchored_inventory_balances(snapshot, orders=orders, anchors=anchors)
    positions = {
        resolved_product_id(position.product_id, snapshot.deployment): (
            position.quantity if position.side is PositionSide.LONG else -position.quantity
        )
        for position in snapshot_positions(snapshot)
    }
    return tuple(
        sorted(
            product_id
            for product_id in anchors
            if (balance := balances.get(product_id, Decimal(0))) != 0
            and (
                balance * positions.get(product_id, Decimal(0)) <= 0
                or abs(balance) > abs(positions.get(product_id, Decimal(0)))
            )
        )
    )


def _anchored_inventory_balances(
    snapshot: DeploymentSnapshot,
    *,
    orders: dict[UUID, Order],
    anchors: dict[str, datetime],
) -> dict[str, Decimal]:
    """Net applied fills from the first owned entry order, excluding legacy exit orders.

    Anchor on durable local order creation, not fill publication or a possibly identical
    venue fill timestamp. An older exit order cannot establish that a later owned entry
    was projected/settled; ambiguous late executions keep this predicate conservative.
    """
    balances: dict[str, Decimal] = {}
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if fill.economics_applied_at is None or order is None:
            continue
        product_id = resolved_product_id(order.product_id, snapshot.deployment)
        anchor = anchors.get(product_id)
        if anchor is None or order.created_at < anchor:
            continue
        signed = fill.quantity if order.side is OrderSide.BUY else -fill.quantity
        balances[product_id] = balances.get(product_id, Decimal(0)) + signed
    return balances


def fill_projection_deployment(
    before: DeploymentSnapshot, projected: DeploymentSnapshot
) -> Deployment:
    """Merge shared fill changes without replacing a multi-book decision cursor.

    The projection's deployment is focused on the filled product. The parent keeps
    its scheduling fields while its phase reflects all persisted product runtimes.
    """
    if not projected.instrument_runtimes:
        return projected.deployment
    return replace(
        before.deployment,
        cash=projected.deployment.cash,
        status=projected.deployment.status,
        mismatch_detail=projected.deployment.mismatch_detail,
        phase=aggregate_phase(projected.instrument_runtimes),
        updated_at=projected.deployment.updated_at,
    )


def prior_fills_for_order(snapshot: DeploymentSnapshot, order_id: UUID) -> int:
    """Count fills already recorded for one order (including unapplied evidence)."""
    return sum(1 for fill in snapshot.fills if fill.order_id == order_id)


def project_fill_economics(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    cooldown_bars: int = 0,
    timeframe: str | None = None,
) -> tuple[DeploymentSnapshot, Fill]:
    """Apply one fill to its own product book without persisting.

    Atomic stores load the full deployment, so the product must be selected here,
    not merely in the caller's overlay. Sibling positions are never the exit target.
    """
    product_id = resolved_product_id(order.product_id, snapshot.deployment)
    focused = overlay_snapshot(snapshot, product_id)
    projected, stamped = _project_inventory(
        focused, fill=fill, order=order, cooldown_bars=cooldown_bars, timeframe=timeframe
    )
    if not snapshot.instrument_runtimes:
        return replace(projected, instrument_runtimes=()), stamped
    runtime = runtime_from_deployment(projected.deployment, product_id)
    siblings = tuple(item for item in snapshot.instrument_runtimes if item.product_id != product_id)
    return replace(projected, instrument_runtimes=(*siblings, runtime)), stamped


def _project_inventory(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    cooldown_bars: int,
    timeframe: str | None,
) -> tuple[DeploymentSnapshot, Fill]:
    """Project one already-focused inventory change and preserve exact cash/fee economics."""
    now = utc_now()
    stamped = (
        fill if fill.economics_applied_at is not None else replace(fill, economics_applied_at=now)
    )
    position = snapshot.position
    if position is None:
        return (
            _project_entry(snapshot, fill=stamped, order=order, now=now, timeframe=timeframe),
            stamped,
        )
    scaling_in = (position.side is PositionSide.LONG and order.side is OrderSide.BUY) or (
        position.side is PositionSide.SHORT and order.side is OrderSide.SELL
    )
    if scaling_in:
        return (
            _project_scale_in(snapshot, fill=stamped, order=order, position=position, now=now),
            stamped,
        )
    return (
        _project_exit(snapshot, fill=stamped, order=order, cooldown_bars=cooldown_bars, now=now),
        stamped,
    )


def _project_entry(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    now: datetime,
    timeframe: str | None = None,
) -> DeploymentSnapshot:
    """Open a long from a buy fill or a short from a sell fill."""
    deployment = snapshot.deployment
    side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    # Shutdown remains durable even if a late entry fill lacks projection metadata.
    fault_status = (
        DeploymentStatus.STOPPED
        if deployment.status is DeploymentStatus.STOPPED
        else DeploymentStatus.PAUSED
    )
    stop = deployment.pending_stop_price
    # A None target is legal: the strategy declares no take-profit (ADR 0090), and the
    # book is protected by its stop alone. The stop is always required.
    target = deployment.pending_target_price
    product_id = resolved_product_id(order.product_id, deployment)
    if stop is None:
        paused = with_runtime(
            deployment,
            updated_at=now,
            cash=cash,
            status=fault_status,
            mismatch_detail="Entry fill is missing its stored stop price.",
            phase=RuntimePhase.FLAT,
            clear_pending_levels=True,
        )
        return DeploymentSnapshot(
            deployment=paused,
            position=None,
            orders=snapshot.orders,
            fills=_upsert_fill(snapshot.fills, fill),
            intents=snapshot.intents,
            positions=(),
            instrument_runtimes=snapshot.instrument_runtimes,
        )
    bar_timeframe = deployment.timeframe or timeframe
    if bar_timeframe is None:
        paused = with_runtime(
            deployment,
            updated_at=now,
            cash=cash,
            status=fault_status,
            mismatch_detail="Entry fill is missing a deployment timeframe for bar bucketing.",
            phase=RuntimePhase.FLAT,
            clear_pending_levels=True,
        )
        return DeploymentSnapshot(
            deployment=paused,
            position=None,
            orders=snapshot.orders,
            fills=_upsert_fill(snapshot.fills, fill),
            intents=snapshot.intents,
            positions=(),
            instrument_runtimes=snapshot.instrument_runtimes,
        )
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
        product_id=product_id,
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
    positions = _replace_position(snapshot.positions, position, product_id)
    return DeploymentSnapshot(
        deployment=updated,
        position=position,
        orders=snapshot.orders,
        fills=_upsert_fill(snapshot.fills, fill),
        intents=snapshot.intents,
        positions=positions,
        instrument_runtimes=snapshot.instrument_runtimes,
    )


def _project_scale_in(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    position: Position,
    now: datetime,
) -> DeploymentSnapshot:
    """Add to an existing position on the same side."""
    deployment = snapshot.deployment
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
    product_id = resolved_product_id(updated_position.product_id, deployment)
    positions = _replace_position(snapshot.positions, updated_position, product_id)
    return DeploymentSnapshot(
        deployment=updated,
        position=updated_position,
        orders=snapshot.orders,
        fills=_upsert_fill(snapshot.fills, fill),
        intents=snapshot.intents,
        positions=positions,
        instrument_runtimes=snapshot.instrument_runtimes,
    )


def _project_exit(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    cooldown_bars: int,
    now: datetime,
) -> DeploymentSnapshot:
    """Reduce or flatten the open position after a covering fill."""
    deployment = snapshot.deployment
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    position = snapshot.position
    product_id = (
        resolved_product_id(position.product_id, deployment) if position is not None else None
    )
    if position is not None and fill.quantity < position.quantity:
        remaining = replace(
            position,
            quantity=position.quantity - fill.quantity,
            updated_at=now,
        )
        updated = with_runtime(deployment, updated_at=now, cash=cash)
        positions = _replace_position(snapshot.positions, remaining, product_id or "")
        return DeploymentSnapshot(
            deployment=updated,
            position=remaining,
            orders=snapshot.orders,
            fills=_upsert_fill(snapshot.fills, fill),
            intents=snapshot.intents,
            positions=positions,
            instrument_runtimes=snapshot.instrument_runtimes,
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
    positions = tuple(
        item
        for item in snapshot.positions
        if product_id is None or resolved_product_id(item.product_id, deployment) != product_id
    )
    return DeploymentSnapshot(
        deployment=updated,
        position=None,
        orders=snapshot.orders,
        fills=_upsert_fill(snapshot.fills, fill),
        intents=snapshot.intents,
        positions=positions,
        instrument_runtimes=snapshot.instrument_runtimes,
    )


def _replace_position(
    positions: tuple[Position, ...],
    position: Position,
    product_id: str,
) -> tuple[Position, ...]:
    """Replace one product book inside a multi-book tuple."""
    others = tuple(item for item in positions if (item.product_id or product_id) != product_id)
    return (*others, position)


def _upsert_fill(fills: tuple[Fill, ...], fill: Fill) -> tuple[Fill, ...]:
    """Return fills with one row replaced or appended."""
    for index, item in enumerate(fills):
        if item.venue_fill_id == fill.venue_fill_id and item.deployment_id == fill.deployment_id:
            return tuple(
                fill if position == index else fills[position] for position in range(len(fills))
            )
    return (*fills, fill)


async def ingest_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int = 0,
    timeframe: str | None = None,
) -> FillIngestResult:
    """Persist fill evidence and apply economics exactly once."""
    bar_timeframe = snapshot.deployment.timeframe or timeframe
    apply_transaction = getattr(store, "apply_fill_transaction", None)
    if apply_transaction is not None:
        applied, updated = await apply_transaction(
            snapshot.deployment.id,
            fill=fill,
            order=order,
            cooldown_bars=cooldown_bars,
            timeframe=bar_timeframe,
        )
        return FillIngestResult(applied=applied, snapshot=updated)
    existing = next(
        (
            item
            for item in snapshot.fills
            if item.venue_fill_id == fill.venue_fill_id and item.deployment_id == fill.deployment_id
        ),
        None,
    )
    if existing is not None and existing.economics_applied_at is not None:
        return FillIngestResult(applied=False, snapshot=snapshot)
    projected, stamped = project_fill_economics(
        snapshot,
        fill=fill,
        order=order,
        cooldown_bars=cooldown_bars,
        timeframe=bar_timeframe,
    )
    await store.save_fill(stamped)
    product_id = resolved_product_id(order.product_id, snapshot.deployment)
    if projected.position is not None:
        await store.save_position(
            projected.position, deployment_id=snapshot.deployment.id, product_id=product_id
        )
    elif projected.deployment.phase is RuntimePhase.FLAT:
        await store.save_position(None, deployment_id=snapshot.deployment.id, product_id=product_id)
    for runtime in projected.instrument_runtimes:
        if runtime.product_id == product_id:
            await store.save_instrument_runtime(runtime, deployment_id=snapshot.deployment.id)
    await store.save_deployment(fill_projection_deployment(snapshot, projected))
    refreshed = await store.get_deployment(snapshot.deployment.id)
    return FillIngestResult(applied=True, snapshot=refreshed)


async def replay_unapplied_fills(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    cooldown_bars: int = 0,
    timeframe: str | None = None,
) -> DeploymentSnapshot:
    """Apply any persisted fills whose economics were never committed."""
    current = snapshot
    pending = tuple(fill for fill in current.fills if fill.economics_applied_at is None)
    for fill in pending:
        order = next((item for item in current.orders if item.id == fill.order_id), None)
        if order is None:
            continue
        result = await ingest_fill(
            current,
            fill=fill,
            order=order,
            store=store,
            cooldown_bars=cooldown_bars,
            timeframe=timeframe,
        )
        current = result.snapshot
    return current
