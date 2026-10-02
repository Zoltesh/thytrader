"""Observable per-product protection status from a deployment snapshot.

HTTP and operator reports classify live cover from verified attached-child tracking
and venue-visible resting exits. Parent stop/target geometry is never coverage. An
open paper book is always covered: the worker enforces its stop synthetically on every
closed bar and any take-profit rests in the paper broker (ADR 0097, ADR 0098), so
``protection_status`` and ``position_state`` agree on every read, full or bounded.

``position_state`` and ``exit_in_flight`` (ADR 0097) split the worker's internal
``pending_exit`` phase, which is set as soon as any exit order works (including the TP/SL
bracket or ADR 0090 stop-only protection resting right after entry), into an open,
protected book and a book whose exit is actually being sent.
"""

from __future__ import annotations

from enum import StrEnum

from thytrader.execution.attached import (
    attached_entry_covers,
    child_order_for,
    filled_attached_entry,
)
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
)

_ACTIVE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN})
_WORKING_STATUSES = _ACTIVE_STATUSES
_PROTECTIVE_PURPOSES = frozenset(
    {
        IntentPurpose.STOP,
        IntentPurpose.TAKE_PROFIT,
        IntentPurpose.BRACKET,
        IntentPurpose.TIME_EXIT,
        IntentPurpose.SIGNAL_EXIT,
    }
)


class ProtectionStatus(StrEnum):
    """Whether one product book currently shows venue-visible exit cover."""

    FLAT = "flat"
    COVERED = "covered"
    UNPROTECTED = "unprotected"
    UNKNOWN = "unknown"


class PositionState(StrEnum):
    """What a book (or a whole deployment) is doing, in operator terms (ADR 0097).

    ``open_protected`` is an open book whose exit cover rests (TP/SL bracket, stop-only
    protection, or the paper synthetic stop with any resting take-profit); the raw
    ``phase`` reads ``pending_exit`` for it. ``exiting`` is a book whose exit is in flight:
    a marketable exit order is working, a signal exit matched, or a flatten was asked.
    """

    FLAT = "flat"
    ENTERING = "entering"
    OPEN_PROTECTED = "open_protected"
    OPEN_UNPROTECTED = "open_unprotected"
    OPEN_UNVERIFIED = "open_unverified"
    EXITING = "exiting"


_STATE_PRIORITY: tuple[PositionState, ...] = (
    PositionState.EXITING,
    PositionState.OPEN_UNPROTECTED,
    PositionState.OPEN_UNVERIFIED,
    PositionState.OPEN_PROTECTED,
)
_OPEN_STATE: dict[ProtectionStatus, PositionState] = {
    ProtectionStatus.FLAT: PositionState.FLAT,
    ProtectionStatus.COVERED: PositionState.OPEN_PROTECTED,
    ProtectionStatus.UNPROTECTED: PositionState.OPEN_UNPROTECTED,
    ProtectionStatus.UNKNOWN: PositionState.OPEN_UNVERIFIED,
}


def book_exit_in_flight(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
) -> bool:
    """Whether this book is exiting rather than resting protection.

    True when a flatten was asked, the ``exits.signal_exit`` marker is set (ADR 0093), or
    an active marketable order on the closing side works for this product. Resting
    take-profits, brackets, and stop-limits are protection, not an exit in flight. Uses
    order sides rather than intents so bounded summary reads (open orders, no intents)
    classify the same way.
    """
    if position is None:
        return False
    if snapshot.deployment.lifecycle_command is LifecycleCommand.FLATTEN:
        return True
    if position.signal_exit_bar is not None:
        return True
    closing = _closing_side(position)
    scoped_product = resolved_product_id(product_id or position.product_id, snapshot.deployment)
    return any(
        order.kind is OrderKind.MARKETABLE
        and order.side is closing
        and order.status in _ACTIVE_STATUSES
        and resolved_product_id(order.product_id, snapshot.deployment) == scoped_product
        for order in snapshot.orders
    )


def book_position_state(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
    phase: RuntimePhase,
) -> PositionState:
    """Classify one product book as flat, entering, open (by protection), or exiting.

    A paper book that is not exiting is always ``open_protected``: the worker enforces its
    stop synthetically on every closed bar and its take-profit, if any, rests in the paper
    broker. Live books map ``protection_status`` (covered, unprotected, unknown).
    """
    if position is None:
        return PositionState.ENTERING if phase is RuntimePhase.PENDING_ENTRY else PositionState.FLAT
    if book_exit_in_flight(snapshot, product_id=product_id, position=position):
        return PositionState.EXITING
    if snapshot.deployment.mode is DeploymentMode.PAPER:
        return PositionState.OPEN_PROTECTED
    status = book_protection_status(snapshot, product_id=product_id, position=position)
    return _OPEN_STATE[status]


def deployment_position_state(snapshot: DeploymentSnapshot) -> PositionState:
    """Collapse every book: exiting, then unprotected, unverified, protected; else entering."""
    states = {
        book_position_state(
            snapshot,
            product_id=resolved_product_id(position.product_id, snapshot.deployment),
            position=position,
            phase=snapshot.deployment.phase,
        )
        for position in snapshot_positions(snapshot)
    }
    for state in _STATE_PRIORITY:
        if state in states:
            return state
    if snapshot.deployment.phase is RuntimePhase.PENDING_ENTRY:
        return PositionState.ENTERING
    return PositionState.FLAT


def deployment_exit_in_flight(snapshot: DeploymentSnapshot) -> bool:
    """Whether any book of the deployment is exiting (see ``book_exit_in_flight``)."""
    return deployment_position_state(snapshot) is PositionState.EXITING


def book_protection_status(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
) -> ProtectionStatus:
    """Classify protection for one product book from persisted orders and intents.

    Paper books are ``covered`` whether or not a take-profit rests (ADR 0098): the worker
    enforces the stop synthetically on every closed bar (ADR 0090), the same rule
    ``book_position_state`` applies. ``unknown`` is only for unreconciled live protective
    orders. A missing or canceled attached child is ``unprotected``, not ``unknown``.
    """
    if position is None:
        return ProtectionStatus.FLAT
    if snapshot.deployment.mode is DeploymentMode.PAPER:
        return ProtectionStatus.COVERED
    if attached_entry_covers(snapshot, position):
        entry = filled_attached_entry(snapshot, position)
        if entry is not None:
            child = child_order_for(snapshot, entry)
            if child is not None and child.status is OrderStatus.UNKNOWN:
                return ProtectionStatus.UNKNOWN
        return ProtectionStatus.COVERED
    scoped_product = resolved_product_id(product_id or position.product_id, snapshot.deployment)
    protective = _protective_orders(snapshot, scoped_product, closing=_closing_side(position))
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


def _closing_side(position: Position) -> OrderSide:
    """The order side that reduces this book (sell for a long, buy for a short)."""
    return OrderSide.BUY if position.side is PositionSide.SHORT else OrderSide.SELL


def _protective_orders(
    snapshot: DeploymentSnapshot, product_id: str, *, closing: OrderSide
) -> tuple[Order, ...]:
    """Return active stop, take-profit, bracket, time-, or signal-exit orders for one product.

    Full reads name each order's purpose through its intent. Bounded summary reads carry
    open orders without intents, so an active closing-side order whose intent is not in the
    snapshot counts as protective there: only exits and protection reduce an open book.
    """
    known_intents = {intent.id for intent in snapshot.intents}
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
        unlabeled_exit = order.intent_id not in known_intents and order.side is closing
        if purpose is not None or is_venue_protection(order.kind) or unlabeled_exit:
            matching.append(order)
    return tuple(matching)
