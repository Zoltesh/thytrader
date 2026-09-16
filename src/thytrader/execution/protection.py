"""Observable per-product protection status from a deployment snapshot.

This classification is HTTP/operator-facing. It does not submit orders or change
runtime state. Paper synthetic stops that are not venue-visible appear
unprotected until a resting exit or attached bracket is on the snapshot.
"""

from __future__ import annotations

from datetime import UTC
from enum import StrEnum

from thytrader.execution.models import (
    DeploymentSnapshot,
    Fill,
    IntentPurpose,
    Order,
    OrderKind,
    OrderStatus,
    Position,
    resolved_product_id,
)

_ACTIVE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN})
_WORKING_STATUSES = _ACTIVE_STATUSES
_ENTRY_KINDS = frozenset({OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE})
_PROTECTIVE_PURPOSES = frozenset(
    {
        IntentPurpose.STOP,
        IntentPurpose.TAKE_PROFIT,
        IntentPurpose.BRACKET,
        IntentPurpose.TIME_EXIT,
    }
)


class ProtectionStatus(StrEnum):
    """Whether one product book currently shows venue-visible exit cover."""

    FLAT = "flat"
    COVERED = "covered"
    UNPROTECTED = "unprotected"
    UNKNOWN = "unknown"


def book_protection_status(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
) -> ProtectionStatus:
    """Classify protection for one product book from persisted orders and intents."""
    if position is None:
        return ProtectionStatus.FLAT
    scoped_product = resolved_product_id(product_id or position.product_id, snapshot.deployment)
    if _attached_entry_covers_product(snapshot, position, scoped_product):
        return ProtectionStatus.COVERED
    protective = _protective_orders(snapshot, scoped_product)
    if not protective:
        return ProtectionStatus.UNPROTECTED
    known_working = tuple(
        order for order in protective if order.status in {OrderStatus.OPEN, OrderStatus.PENDING}
    )
    if known_working:
        return ProtectionStatus.COVERED
    if any(order.status is OrderStatus.UNKNOWN for order in protective):
        return ProtectionStatus.UNKNOWN
    return ProtectionStatus.UNPROTECTED


def working_order_count(orders: tuple[Order, ...]) -> int:
    """Count orders that are still in the reconcile watch set."""
    return sum(1 for order in orders if order.status in _WORKING_STATUSES)


def _protective_orders(snapshot: DeploymentSnapshot, product_id: str) -> tuple[Order, ...]:
    """Return active stop, take-profit, bracket, or time-exit orders for one product."""
    purposes = {
        intent.id: intent.purpose
        for intent in snapshot.intents
        if intent.purpose in _PROTECTIVE_PURPOSES
    }
    matching: list[Order] = []
    for order in snapshot.orders:
        if resolved_product_id(order.product_id, snapshot.deployment) != product_id:
            continue
        if order.status not in _ACTIVE_STATUSES:
            continue
        purpose = purposes.get(order.intent_id)
        if purpose is not None or order.kind is OrderKind.TRIGGER_BRACKET:
            matching.append(order)
    return tuple(matching)


def _attached_entry_covers_product(
    snapshot: DeploymentSnapshot, position: Position, product_id: str
) -> bool:
    """True when a filled attached entry still matches this book's stop and target."""
    entry = _filled_attached_entry(snapshot, position, product_id)
    if entry is None:
        return False
    child_id = entry.attached_child_venue_order_id
    if not child_id:
        return False
    child = next(
        (order for order in snapshot.orders if order.venue_order_id == child_id),
        None,
    )
    if child is None or child.status not in {OrderStatus.OPEN, OrderStatus.PENDING}:
        return False
    return (
        child.stop_trigger_price == position.stop_price
        and child.take_profit_price == position.target_price
        and child.quantity >= position.quantity
    )


def _filled_attached_entry(
    snapshot: DeploymentSnapshot, position: Position, product_id: str
) -> Order | None:
    """Return the filled entry that opened this product book with bracket prices."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    fills_by_order: dict[object, list[Fill]] = {}
    for fill in snapshot.fills:
        fills_by_order.setdefault(fill.order_id, []).append(fill)
    for order in snapshot.orders:
        if resolved_product_id(order.product_id, snapshot.deployment) != product_id:
            continue
        if order.status is not OrderStatus.FILLED:
            continue
        if order.kind not in _ENTRY_KINDS:
            continue
        if order.stop_trigger_price is None or order.take_profit_price is None:
            continue
        if entry_ids and order.intent_id not in entry_ids:
            continue
        order_fills = fills_by_order.get(order.id, ())
        if any(_fill_opened_position(item, position) for item in order_fills):
            return order
    return None


def _fill_opened_position(fill: Fill, position: Position) -> bool:
    """True when this fill is the entry that opened the current position."""
    entered = fill.filled_at.astimezone(UTC).replace(second=0, microsecond=0)
    return entered == position.entered_bar and fill.price == position.entry_price
