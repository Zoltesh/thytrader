"""Observable per-product protection status from a deployment snapshot.

HTTP and operator reports classify cover from verified attached-child tracking
and venue-visible resting exits. Parent stop/target geometry is never coverage.
Paper synthetic stops that are not venue-visible appear unprotected.
"""

from __future__ import annotations

from enum import StrEnum

from thytrader.execution.attached import (
    attached_entry_covers,
    child_order_for,
    filled_attached_entry,
)
from thytrader.execution.models import (
    DeploymentSnapshot,
    IntentPurpose,
    Order,
    OrderKind,
    OrderStatus,
    Position,
    resolved_product_id,
)

_ACTIVE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN})
_WORKING_STATUSES = _ACTIVE_STATUSES
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
    """Classify protection for one product book from persisted orders and intents.

    ``unknown`` is only for unreconciled protective orders. A missing or canceled
    attached child is ``unprotected``, not ``unknown``.
    """
    if position is None:
        return ProtectionStatus.FLAT
    if attached_entry_covers(snapshot, position):
        entry = filled_attached_entry(snapshot, position)
        if entry is not None:
            child = child_order_for(snapshot, entry)
            if child is not None and child.status is OrderStatus.UNKNOWN:
                return ProtectionStatus.UNKNOWN
        return ProtectionStatus.COVERED
    scoped_product = resolved_product_id(product_id or position.product_id, snapshot.deployment)
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
