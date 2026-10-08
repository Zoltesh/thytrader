"""Observable per-product protection status from a deployment snapshot.

HTTP and operator reports classify live cover from freshly observed OPEN venue-identified
stops whose persisted closing side, remaining quantity, and executable geometry match the book.
Only venue_observed_at supplies order-state freshness; local writes never renew it. Receipt
of order state is not an independent venue geometry or account audit. A take-profit alone
is not cover. Pending and unknown orders are not confirmed cover. Parent
stop/target geometry is never coverage, and an attached child does not bypass those
checks. A resolved open paper book stays ``covered`` because the worker enforces its stop
synthetically, but the evidence says that cover is worker-dependent rather than a
venue-resting order (ADR 0098, ADR 0112).

``position_state`` and ``exit_in_flight`` (ADR 0097) split the worker's internal
``pending_exit`` phase, which is set as soon as any exit order works, into an open
protected book and a book whose exit is actually being sent.

This module holds the public entry points. The vocabulary and evidence values live in
``protection_models``, the per-book evidence builders in ``protection_evidence`` and the
per-order classification in ``protection_orders``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.trading.fill_ledger import (
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
    Position,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection_evidence import (
    _closing_side,
    _flat_evidence,
    _live_evidence,
    _paper_evidence,
    _unresolved_evidence,
)
from thytrader.trading.protection_models import (
    _ACTIVE_STATUSES,
    _OPEN_STATE,
    _STATE_PRIORITY,
    _WORKING_STATUSES,
    PositionState,
    ProtectionEvidence,
    ProtectionEvidenceResponse,
    ProtectionStatus,
)
from thytrader.trading.protection_orders import _aware

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal


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
    evidence: ProtectionEvidence | None = None,
) -> PositionState:
    """Classify one product book as flat, entering, open (by protection), or exiting.

    A resolved paper book that is not exiting is ``open_protected``: the worker enforces
    its stop synthetically on every closed bar. Unresolved economics stays unverified.
    Live books map ``protection_status``. A take-profit alone is not covered, so it is
    ``open_unprotected``.
    """
    observed = evidence or book_protection_evidence(
        snapshot, product_id=product_id, position=position
    )
    if book_exit_in_flight(snapshot, product_id=product_id, position=position):
        return PositionState.EXITING
    if observed.required_quantity is None:
        return PositionState.OPEN_UNVERIFIED
    if position is None:
        return PositionState.ENTERING if phase is RuntimePhase.PENDING_ENTRY else PositionState.FLAT
    if snapshot.deployment.mode is DeploymentMode.PAPER:
        return PositionState.OPEN_PROTECTED
    return _OPEN_STATE[observed.status]


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
    if (
        unprojected_inventory_products(snapshot)
        or missing_occupied_inventory_products(snapshot)
        or unsettled_fill_evidence(snapshot)
        or not snapshot.accounting_complete
    ):
        states.add(PositionState.OPEN_UNVERIFIED)
    for state in _STATE_PRIORITY:
        if state in states:
            return state
    if snapshot.deployment.phase is RuntimePhase.PENDING_ENTRY:
        return PositionState.ENTERING
    return PositionState.FLAT


def deployment_exit_in_flight(snapshot: DeploymentSnapshot) -> bool:
    """Whether any book of the deployment is exiting (see ``book_exit_in_flight``)."""
    return deployment_position_state(snapshot) is PositionState.EXITING


def book_protection_evidence(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
    now: datetime | None = None,
) -> ProtectionEvidence:
    """Return quantitative cover for one book without inventing prices or timestamps.

    Live cover counts only unique open stop orders on the closing side whose remaining
    quantity and stop geometry match the book. The same attached child is counted once.
    Pending and unknown orders never add confirmed quantity. Paper cover is the worker's
    synthetic stop and is labeled as such.
    """
    evaluated = _aware(now or utc_now())
    if evaluated is None:
        raise ValueError("Protection reporting requires an aware UTC clock.")
    reasons = book_inventory_reasons(snapshot, product_id=product_id)
    if not reasons and position is None and not snapshot.accounting_complete:
        reasons = ("inventory_evidence_incomplete",)
    if reasons:
        return _unresolved_evidence(evaluated, reasons)
    if position is None or position.quantity <= 0:
        return _flat_evidence(evaluated)
    if snapshot.deployment.mode is DeploymentMode.PAPER:
        return _paper_evidence(position, evaluated)
    return _live_evidence(snapshot, product_id, position, evaluated)


def live_stop_absent(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
    now: datetime | None = None,
) -> bool:
    """Whether a live open book has no confirmed, pending, or unknown stop at all.

    Unresolved inventory leaves :func:`book_protection_evidence` unknown because the required
    quantity is unknown, but no working stop is uncovered at any positive quantity.
    """
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        return False
    if position is None or position.quantity <= 0:
        return False
    evaluated = _aware(now or utc_now())
    if evaluated is None:
        raise ValueError("Protection reporting requires an aware UTC clock.")
    evidence = _live_evidence(snapshot, product_id, position, evaluated)
    return "no_resting_stop" in evidence.reasons


def book_protection_status(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
) -> ProtectionStatus:
    """Classify protection for one product book from persisted orders and intents.

    Resolved paper books are ``covered`` whether or not a take-profit rests (ADR 0098). Live
    ``covered`` requires a confirmed open stop of sufficient remaining quantity and valid
    geometry. A take-profit alone, a pending stop, and an unknown stop are not covered.
    """
    return book_protection_evidence(snapshot, product_id=product_id, position=position).status


def protection_evidence_response(evidence: ProtectionEvidence) -> ProtectionEvidenceResponse:
    """Serialize one evidence value with exact decimal strings and unknown times as null."""
    return ProtectionEvidenceResponse(
        required_quantity=_quantity_text(evidence.required_quantity),
        covered_quantity=_quantity_text(evidence.covered_quantity),
        uncovered_quantity=_quantity_text(evidence.uncovered_quantity),
        stop_side=(
            None
            if evidence.stop_side is None
            else "buy"
            if evidence.stop_side is OrderSide.BUY
            else "sell"
        ),
        stop_side_valid=evidence.stop_side_valid,
        stop_geometry_valid=evidence.stop_geometry_valid,
        mechanism=evidence.mechanism.value,
        venue_resting=evidence.venue_resting,
        worker_dependent=evidence.worker_dependent,
        observed_at=_timestamp(evidence.observed_at),
        verified_at=_timestamp(evidence.verified_at),
        observation_source=evidence.observation_source,
        freshness=evidence.freshness,
        evaluated_at=evidence.evaluated_at.isoformat(),
        freshness_max_age_seconds=evidence.freshness_max_age_seconds,
        geometry_basis=evidence.geometry_basis,
        reasons=evidence.reasons,
    )


def book_inventory_reasons(snapshot: DeploymentSnapshot, *, product_id: str) -> tuple[str, ...]:
    """Disclose durable unresolved economics on one product, never an exit quantity.

    Occupied product runtimes without positive projected inventory also prove uncertainty,
    regardless of sibling inventory or deployment status. Orphan unapplied fills cannot
    be assigned a product, so they conservatively leave every book unresolved.
    """
    product = resolved_product_id(product_id, snapshot.deployment)
    reasons: list[str] = []
    if product in unprojected_inventory_products(snapshot):
        reasons.append("inventory_projection_unresolved")
    if product in missing_occupied_inventory_products(snapshot):
        reasons.append("runtime_position_unresolved")
    orders = tuple(
        order
        for order in snapshot.orders
        if resolved_product_id(order.product_id, snapshot.deployment) == product
    )
    ids = {order.id for order in orders}
    known_ids = {order.id for order in snapshot.orders}
    fills = tuple(
        fill for fill in snapshot.fills if fill.order_id in ids or fill.order_id not in known_ids
    )
    if unsettled_fill_evidence(replace(snapshot, orders=orders, fills=fills)):
        reasons.append("fill_economics_unsettled")
    return tuple(reasons)


def missing_occupied_inventory_products(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """Name occupied runtimes missing inventory, without inventing a position or exit size.

    Deployment phase is aggregate, not per-product evidence. Its legacy occupied/empty
    contradiction remains unknown when no positive inventory exists anywhere.
    """
    occupied = {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
    projected = {
        resolved_product_id(position.product_id, snapshot.deployment)
        for position in snapshot_positions(snapshot)
        if position.quantity > 0
    }
    missing = {
        resolved_product_id(runtime.product_id, snapshot.deployment)
        for runtime in snapshot.instrument_runtimes
        if runtime.phase in occupied
        and resolved_product_id(runtime.product_id, snapshot.deployment) not in projected
    }
    if not projected and snapshot.deployment.phase in occupied:
        missing.add(snapshot.deployment.product_id)
    return tuple(sorted(missing))


def _quantity_text(quantity: Decimal | None) -> str | None:
    """Unknown inventory is null, not a zero quantity."""
    return None if quantity is None else format(quantity, "f")


def working_order_count(orders: tuple[Order, ...]) -> int:
    """Count orders that are still in the reconcile watch set."""
    return sum(1 for order in orders if order.status in _WORKING_STATUSES)


def _timestamp(value: datetime | None) -> str | None:
    """ISO-8601 text, or null when the observation is unknown."""
    return None if value is None else value.isoformat()
