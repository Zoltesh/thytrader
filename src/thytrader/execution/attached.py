"""Verified attached-child coverage; inferred parent geometry is not protection."""

from __future__ import annotations

from decimal import Decimal

from thytrader.trading.models import (
    DeploymentSnapshot,
    Fill,
    IntentPurpose,
    Order,
    OrderKind,
    OrderStatus,
    Position,
)

_ACTIVE = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
_ENTRY_KINDS = {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE}


def attached_entry_covers(snapshot: DeploymentSnapshot, position: Position) -> bool:
    """True only when a tracked attached child is active and matches stop, target, and qty.

    A missing, canceled, or rejected child is uncovered. Parent stop/target geometry
    is never treated as coverage.
    """
    entry = filled_attached_entry(snapshot, position)
    if entry is None or not entry.attached_child_venue_order_id:
        return False
    child = child_order_for(snapshot, entry)
    if child is None or child.status not in _ACTIVE:
        return False
    return (
        child.stop_trigger_price == position.stop_price
        and child.take_profit_price == position.target_price
        and child.quantity >= position.quantity
    )


def filled_attached_entry(snapshot: DeploymentSnapshot, position: Position) -> Order | None:
    """Return the filled entry that opened this position with an attached venue bracket."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    fills_by_order: dict[object, list[Fill]] = {}
    for fill in snapshot.fills:
        fills_by_order.setdefault(fill.order_id, []).append(fill)
    for order in snapshot.orders:
        if order.status is not OrderStatus.FILLED:
            continue
        if order.kind not in _ENTRY_KINDS:
            continue
        if order.stop_trigger_price is None or order.take_profit_price is None:
            continue
        if entry_ids and order.intent_id not in entry_ids:
            continue
        order_fills = fills_by_order.get(order.id, ())
        if not any(_fill_opened_position(item, position) for item in order_fills):
            continue
        return order
    return None


def child_order_for(snapshot: DeploymentSnapshot, entry: Order) -> Order | None:
    """Return the persisted child whose venue id matches the parent's attached child."""
    child_id = entry.attached_child_venue_order_id
    if not child_id:
        return None
    for order in snapshot.orders:
        if order.venue_order_id == child_id:
            return order
        if order.parent_order_id == entry.id:
            return order
    return None


def remaining_quantity(order: Order) -> Decimal:
    """Return original quantity minus filled quantity, floored at zero."""
    remaining = order.quantity - order.filled_quantity
    return remaining if remaining > 0 else Decimal("0")


def _fill_opened_position(fill: Fill, position: Position) -> bool:
    """True when this fill is the entry that opened the current position."""
    tzinfo = position.entered_bar.tzinfo
    entered = fill.filled_at.astimezone(tzinfo).replace(second=0, microsecond=0)
    return entered == position.entered_bar and fill.price == position.entry_price
