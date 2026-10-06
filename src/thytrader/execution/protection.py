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
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID  # noqa: TC003 - intent maps are keyed at runtime

from pydantic import BaseModel, ConfigDict, Field

from thytrader.execution.fill_ledger import (
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.execution.ids import utc_now
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
_STOP_PURPOSES = frozenset({IntentPurpose.STOP, IntentPurpose.BRACKET})
_ZERO = Decimal(0)
LOCAL_EVIDENCE_MAX_AGE = timedelta(seconds=120)
"""Reporting recency bound: four default worker polls, never a strategy candle clock."""
_ObservationSource = Literal["venue_order_state", "persisted_order", "synthetic_worker", "none"]
_Freshness = Literal["recent_venue", "stale", "unknown"]
_GeometryBasis = Literal["working_target", "stop_limit_trigger", "unknown"]
PROTECTION_REASONS: tuple[str, ...] = (
    "flat",
    "inventory_projection_unresolved",
    "fill_economics_unsettled",
    "inventory_evidence_incomplete",
    "synthetic_worker_dependent",
    "venue_stop_resting",
    "duplicate_order_ignored",
    "take_profit_only",
    "closing_side_mismatch",
    "stop_price_mismatch",
    "stale_bracket",
    "stop_geometry_invalid",
    "stop_geometry_unknown",
    "unsupported_stop_kind",
    "venue_identity_missing",
    "local_observation_only",
    "venue_evidence_stale",
    "observation_time_unknown",
    "observation_time_future",
    "stop_quantity_short",
    "partial_stop_quantity",
    "pending_not_confirmed",
    "unknown_not_confirmed",
    "no_resting_stop",
)
"""Stable reason codes for protection evidence, in display order."""


class ProtectionStatus(StrEnum):
    """Whether fresh order-state evidence matches persisted stop cover, not a venue audit."""

    FLAT = "flat"
    COVERED = "covered"
    UNPROTECTED = "unprotected"
    UNKNOWN = "unknown"


class ProtectionMechanism(StrEnum):
    """How cover was established, without treating a synthetic stop as a venue order."""

    VENUE = "venue"
    SYNTHETIC = "synthetic"
    NONE = "none"
    UNVERIFIED = "unverified"


class PositionState(StrEnum):
    """What a book (or a whole deployment) is doing, in operator terms (ADR 0097).

    ``open_protected`` has fresh order-state evidence matching persisted stop geometry or
    the paper synthetic stop. Neither proves a venue geometry audit. ``exiting`` is in flight.
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
_HitRole = Literal["confirmed", "pending", "unknown", "ignored"]


@dataclass(frozen=True, slots=True)
class ProtectionEvidence:
    """Exact persisted stop cover, with venue order-state provenance, not geometry auditing."""

    status: ProtectionStatus
    required_quantity: Decimal | None
    covered_quantity: Decimal | None
    uncovered_quantity: Decimal | None
    stop_side: OrderSide | None
    stop_side_valid: bool
    stop_geometry_valid: bool
    mechanism: ProtectionMechanism
    venue_resting: bool
    worker_dependent: bool
    observed_at: datetime | None
    verified_at: datetime | None
    observation_source: _ObservationSource
    freshness: _Freshness
    evaluated_at: datetime
    freshness_max_age_seconds: int
    geometry_basis: _GeometryBasis
    reasons: tuple[str, ...]


class ProtectionEvidenceResponse(BaseModel):
    """Strict protection evidence; exact decimal strings, or null for unresolved inventory."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    required_quantity: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    covered_quantity: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    uncovered_quantity: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    stop_side: Literal["buy", "sell"] | None = Field(
        description="Closing side the stop must use, or null when flat or inventory is unresolved."
    )
    stop_side_valid: bool
    stop_geometry_valid: bool
    mechanism: Literal["venue", "synthetic", "none", "unverified"]
    venue_resting: bool = Field(
        description="Fresh identified OPEN state reads contributed with matching local geometry."
    )
    worker_dependent: bool = Field(
        description="True for the paper synthetic stop. That cover is not a venue order."
    )
    observed_at: str | None = Field(
        description="Latest relevant venue order-state receipt time; never a local row update."
    )
    verified_at: str | None = Field(
        description="Oldest contributing fresh OPEN receipt; not a venue geometry audit."
    )
    observation_source: _ObservationSource
    freshness: _Freshness = Field(
        description="Conservative order-state recency, not geometry auditing."
    )
    evaluated_at: str = Field(
        description="UTC reporting clock used to assess venue order-state age."
    )
    freshness_max_age_seconds: int = Field(gt=0)
    geometry_basis: _GeometryBasis = Field(
        description="Persisted geometry vs working target or stop-limit trigger, not venue audited."
    )
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Hit:
    """One active order's contribution to cover, after identity dedupe."""

    remaining: Decimal
    role: _HitRole
    flags: frozenset[str]
    observed_at: datetime | None
    side_valid: bool = False
    geometry_valid: bool = False


@dataclass(frozen=True, slots=True)
class _Fold:
    """Book-level totals after unique orders are classified."""

    confirmed: Decimal
    pending: bool
    unknown: bool
    flags: frozenset[str]
    observed_at: datetime | None
    verified_at: datetime | None
    side_valid: bool
    geometry_valid: bool


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

    Product filtering preserves unrelated valid books. Orphan unapplied fills cannot
    be assigned a product, so they conservatively leave every book unresolved.
    """
    product = resolved_product_id(product_id, snapshot.deployment)
    reasons: list[str] = []
    if product in unprojected_inventory_products(snapshot):
        reasons.append("inventory_projection_unresolved")
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


def _unresolved_evidence(now: datetime, reasons: tuple[str, ...]) -> ProtectionEvidence:
    """Missing projection/economics supplies neither flatness nor executable geometry."""
    return replace(
        _flat_evidence(now),
        status=ProtectionStatus.UNKNOWN,
        required_quantity=None,
        covered_quantity=None,
        uncovered_quantity=None,
        mechanism=ProtectionMechanism.UNVERIFIED,
        reasons=reasons,
    )


def _quantity_text(quantity: Decimal | None) -> str | None:
    """Unknown inventory is null, not a zero quantity."""
    return None if quantity is None else format(quantity, "f")


def working_order_count(orders: tuple[Order, ...]) -> int:
    """Count orders that are still in the reconcile watch set."""
    return sum(1 for order in orders if order.status in _WORKING_STATUSES)


def _flat_evidence(now: datetime) -> ProtectionEvidence:
    """No inventory, so no stop is required and none is claimed."""
    return ProtectionEvidence(
        status=ProtectionStatus.FLAT,
        required_quantity=_ZERO,
        covered_quantity=_ZERO,
        uncovered_quantity=_ZERO,
        stop_side=None,
        stop_side_valid=False,
        stop_geometry_valid=False,
        mechanism=ProtectionMechanism.NONE,
        venue_resting=False,
        worker_dependent=False,
        observed_at=None,
        verified_at=None,
        observation_source="none",
        freshness="unknown",
        evaluated_at=now,
        freshness_max_age_seconds=int(LOCAL_EVIDENCE_MAX_AGE.total_seconds()),
        geometry_basis="unknown",
        reasons=("flat",),
    )


def _paper_evidence(position: Position, now: datetime) -> ProtectionEvidence:
    """Report the worker synthetic stop without calling it a venue-resting order."""
    geometry_ok = _book_stop_on_protective_side(position)
    reasons = ["synthetic_worker_dependent"]
    if geometry_ok is None:
        reasons.append("stop_geometry_unknown")
    elif not geometry_ok:
        reasons.append("stop_geometry_invalid")
    return ProtectionEvidence(
        status=ProtectionStatus.COVERED,
        required_quantity=position.quantity,
        covered_quantity=position.quantity,
        uncovered_quantity=_ZERO,
        stop_side=_closing_side(position),
        stop_side_valid=True,
        stop_geometry_valid=geometry_ok is True,
        mechanism=ProtectionMechanism.SYNTHETIC,
        venue_resting=False,
        worker_dependent=True,
        observed_at=None,
        verified_at=None,
        observation_source="synthetic_worker",
        freshness="unknown",
        evaluated_at=now,
        freshness_max_age_seconds=int(LOCAL_EVIDENCE_MAX_AGE.total_seconds()),
        geometry_basis="working_target" if geometry_ok is not None else "unknown",
        reasons=_ordered_reasons(set(reasons)),
    )


def _live_evidence(
    snapshot: DeploymentSnapshot, product_id: str, position: Position, now: datetime
) -> ProtectionEvidence:
    """Classify a live book from folded identities and fresh venue order-state evidence."""
    closing = _closing_side(position)
    book_ok = _book_stop_on_protective_side(position)
    scoped = resolved_product_id(product_id or position.product_id, snapshot.deployment)
    orders, duplicate = _deduped_active(snapshot, scoped)
    purposes = {intent.id: intent.purpose for intent in snapshot.intents}
    known = frozenset(purposes)
    hits = tuple(
        _classify_order(
            order,
            position,
            closing=closing,
            book_ok=book_ok,
            purposes=purposes,
            known_intents=known,
            venue_identity=_cover_identity(snapshot, order).startswith("venue:"),
            now=now,
        )
        for order in orders
    )
    fold = _fold_hits(hits, duplicate=duplicate)
    return _evidence_from_fold(position, closing, fold, now)


def _evidence_from_fold(
    position: Position,
    closing: OrderSide,
    fold: _Fold,
    now: datetime,
) -> ProtectionEvidence:
    """Turn folded order hits into status, quantities, and reasons."""
    required = position.quantity
    covered = fold.confirmed if fold.confirmed < required else required
    uncovered = required - covered
    status = _live_status(covered, required, fold)
    return ProtectionEvidence(
        status=status,
        required_quantity=required,
        covered_quantity=covered,
        uncovered_quantity=uncovered,
        stop_side=closing,
        stop_side_valid=fold.side_valid,
        stop_geometry_valid=fold.geometry_valid,
        mechanism=_live_mechanism(status, fold),
        venue_resting=fold.confirmed > _ZERO,
        worker_dependent=False,
        observed_at=fold.observed_at,
        verified_at=fold.verified_at,
        observation_source=(
            "venue_order_state"
            if fold.observed_at is not None
            else "persisted_order"
            if fold.flags
            else "none"
        ),
        freshness=_fold_freshness(fold),
        evaluated_at=now,
        freshness_max_age_seconds=int(LOCAL_EVIDENCE_MAX_AGE.total_seconds()),
        geometry_basis=(
            "working_target" if position.target_price is not None else "stop_limit_trigger"
        )
        if fold.geometry_valid
        else "unknown",
        reasons=_live_reasons(status, fold, covered),
    )


def _live_status(covered: Decimal, required: Decimal, fold: _Fold) -> ProtectionStatus:
    """Full confirmed quantity is covered; otherwise pending/unknown stays unconfirmed."""
    if covered >= required:
        return ProtectionStatus.COVERED
    if fold.pending or fold.unknown:
        return ProtectionStatus.UNKNOWN
    return ProtectionStatus.UNPROTECTED


def _live_mechanism(status: ProtectionStatus, fold: _Fold) -> ProtectionMechanism:
    """Name the source of cover without promoting an unconfirmed order to venue-resting."""
    if status is ProtectionStatus.COVERED or fold.confirmed > _ZERO:
        return ProtectionMechanism.VENUE
    if status is ProtectionStatus.UNKNOWN:
        return ProtectionMechanism.UNVERIFIED
    return ProtectionMechanism.NONE


def _live_reasons(status: ProtectionStatus, fold: _Fold, covered: Decimal) -> tuple[str, ...]:
    """Collect stable reasons. Partial cover is not described as a resting full stop."""
    found = set(fold.flags)
    if status is ProtectionStatus.COVERED:
        found.add("venue_stop_resting")
    elif covered > _ZERO:
        found.add("partial_stop_quantity")
    if fold.pending:
        found.add("pending_not_confirmed")
    if fold.unknown:
        found.add("unknown_not_confirmed")
    if status is ProtectionStatus.UNPROTECTED and covered == _ZERO:
        found.add("no_resting_stop")
    return _ordered_reasons(found)


def _deduped_active(
    snapshot: DeploymentSnapshot, product_id: str
) -> tuple[tuple[Order, ...], bool]:
    """Fold all product observations before status filtering so terminal rows win too."""
    active = tuple(
        order
        for order in snapshot.orders
        if resolved_product_id(order.product_id, snapshot.deployment) == product_id
    )
    chosen: dict[str, Order] = {}
    duplicate = False
    # Venue-time ordering makes folds deterministic, including contradictory partial-fill
    # histories. UNKNOWN/missing provenance is processed first and cannot be washed away.
    ordered = sorted(
        active, key=lambda order: _venue_observed(order) or datetime.min.replace(tzinfo=UTC)
    )
    for order in ordered:
        key = _cover_identity(snapshot, order)
        prior = chosen.get(key)
        if prior is None:
            chosen[key] = order
            continue
        duplicate = True
        chosen[key] = _prefer_duplicate(prior, order)
    return tuple(chosen.values()), duplicate


def _cover_identity(snapshot: DeploymentSnapshot, order: Order) -> str:
    """Identity of one protective order, including a child copied under another local id."""
    if order.venue_order_id:
        return f"venue:{order.venue_order_id}"
    parent_child = _parent_attached_venue_id(snapshot, order)
    if parent_child:
        return f"venue:{parent_child}"
    return f"order:{order.id}"


def _parent_attached_venue_id(snapshot: DeploymentSnapshot, order: Order) -> str | None:
    """Return the parent entry's attached child id when this row is that child."""
    if order.parent_order_id is None:
        return None
    for parent in snapshot.orders:
        if parent.id == order.parent_order_id and parent.attached_child_venue_order_id:
            return parent.attached_child_venue_order_id
    return None


def _prefer_duplicate(left: Order, right: Order) -> Order:
    """Prefer actual venue receipts, never local rewrites; unresolved histories fail closed.

    State reads cannot resolve conflicting submitted geometry or original quantities.
    UNKNOWN/missing provenance is absorbing within a duplicate history. A venue id cannot
    reopen after a terminal state, and remaining quantity cannot grow after a later read.
    """
    smaller = left if _remaining(left) <= _remaining(right) else right
    conflict = replace(smaller, status=OrderStatus.UNKNOWN, venue_observed_at=None)
    if _order_geometry(left) != _order_geometry(right) or left.quantity != right.quantity:
        return conflict
    left_time, right_time = _venue_observed(left), _venue_observed(right)
    if left_time is None or right_time is None:
        return conflict
    if left_time == right_time:
        return smaller if left.status is right.status else conflict
    newer, older = (left, right) if left_time > right_time else (right, left)
    if older.status not in _ACTIVE_STATUSES and newer.status in _ACTIVE_STATUSES:
        return conflict
    if _remaining(newer) > _remaining(older):
        return conflict
    return newer


def _classify_order(
    order: Order,
    position: Position,
    *,
    closing: OrderSide,
    book_ok: bool | None,
    purposes: dict[UUID, IntentPurpose],
    known_intents: frozenset[UUID],
    venue_identity: bool,
    now: datetime,
) -> _Hit:
    """Classify one active order. Confirmed cover requires an open matching stop."""
    observed = _venue_observed(order)
    if order.status not in _ACTIVE_STATUSES:
        return _Hit(_ZERO, "ignored", frozenset(), None)
    if order.side is not closing:
        flags = _wrong_side_flags(order, purposes)
        return _Hit(_ZERO, "ignored", flags, None)
    if _take_profit_only(order, purposes, known_intents):
        return _Hit(_ZERO, "ignored", frozenset({"take_profit_only"}), None)
    if not _stop_shaped(order):
        flags = _unsupported_kind_flags(order, purposes)
        role: _HitRole = "unknown" if order.status is OrderStatus.UNKNOWN and flags else "ignored"
        return _Hit(_ZERO, role, flags, None)
    geometry = _geometry_flags(order, position, book_ok=book_ok)
    if order.status is OrderStatus.UNKNOWN:
        flags = _observation_flags(observed, now, venue_identity=venue_identity)
        return _Hit(
            _ZERO,
            "unknown",
            flags | geometry | frozenset({"unknown_not_confirmed"}),
            observed,
            side_valid=True,
            geometry_valid=not geometry,
        )
    if geometry:
        return _Hit(_ZERO, _unreadable_role(order, geometry), geometry, None, side_valid=True)
    remaining = _remaining(order)
    if remaining <= 0:
        return _Hit(
            _ZERO,
            "ignored",
            frozenset({"stop_quantity_short"}),
            None,
            side_valid=True,
            geometry_valid=True,
        )
    flags = _observation_flags(observed, now, venue_identity=venue_identity)
    role = _open_role(order)
    if flags and role == "confirmed":
        role = "unknown"
    if observed is None:
        flags = flags | frozenset({"local_observation_only"})
    return _Hit(remaining, role, flags, observed, side_valid=True, geometry_valid=True)


def _wrong_side_flags(order: Order, purposes: dict[UUID, IntentPurpose]) -> frozenset[str]:
    """Flag an opening-side order only when its purpose or kind looks protective."""
    purpose = purposes.get(order.intent_id)
    if purpose in _STOP_PURPOSES or purpose is IntentPurpose.TAKE_PROFIT:
        return frozenset({"closing_side_mismatch"})
    if is_venue_protection(order.kind):
        return frozenset({"closing_side_mismatch"})
    return frozenset()


def _unreadable_role(order: Order, flags: frozenset[str]) -> _HitRole:
    """An unknown order with no readable stop stays unconfirmed; a bad stop does not."""
    if "stop_geometry_unknown" in flags:
        return "unknown"
    if order.status is OrderStatus.UNKNOWN and "unknown_not_confirmed" in flags:
        return "unknown"
    return "ignored"


def _open_role(order: Order) -> _HitRole:
    """Only OPEN adds confirmed quantity. Pending and unknown stay unconfirmed."""
    if order.status is OrderStatus.OPEN:
        return "confirmed"
    if order.status is OrderStatus.PENDING:
        return "pending"
    if order.status is OrderStatus.UNKNOWN:
        return "unknown"
    return "ignored"


def _geometry_flags(order: Order, position: Position, *, book_ok: bool | None) -> frozenset[str]:
    """Return why this stop does not match the book, or empty when it does."""
    if book_ok is False:
        return frozenset({"stop_geometry_invalid"})
    if order.stop_trigger_price is None:
        return _missing_trigger_flags(order)
    if order.stop_trigger_price != position.stop_price:
        return _price_mismatch_flags(order)
    if not _target_matches(order, position):
        return frozenset({"stale_bracket"})
    if order.kind is OrderKind.STOP_LIMIT:
        return _stop_limit_flags(order, position)
    if book_ok is None:
        return frozenset({"stop_geometry_unknown"})
    return frozenset()


def _missing_trigger_flags(order: Order) -> frozenset[str]:
    """A missing stop price is unconfirmed only while the order itself is unknown."""
    if order.status is OrderStatus.UNKNOWN:
        return frozenset({"unknown_not_confirmed"})
    return frozenset({"stop_geometry_invalid"})


def _price_mismatch_flags(order: Order) -> frozenset[str]:
    """A different stop price is a stale bracket when the order also carries a target."""
    flags = {"stop_price_mismatch"}
    if order.kind is OrderKind.TRIGGER_BRACKET or order.take_profit_price is not None:
        flags.add("stale_bracket")
    return frozenset(flags)


def _target_matches(order: Order, position: Position) -> bool:
    """Whether a bracket's target matches the book. Stop-only orders have no target."""
    if order.kind is OrderKind.STOP_LIMIT:
        return order.take_profit_price is None
    observed = _bracket_target(order)
    if observed is None:
        return order.kind is not OrderKind.TRIGGER_BRACKET
    if position.target_price is None or observed != position.target_price:
        return False
    return order.price is None or order.price == position.target_price


def _bracket_target(order: Order) -> Decimal | None:
    """Target recorded on a bracket: explicit take-profit, else the bracket limit price."""
    if order.take_profit_price is not None:
        return order.take_profit_price
    if order.kind is OrderKind.TRIGGER_BRACKET:
        return order.price
    return None


def _take_profit_only(
    order: Order,
    purposes: dict[UUID, IntentPurpose],
    known_intents: frozenset[UUID],
) -> bool:
    """True for a closing limit or take-profit intent that does not carry a stop."""
    if purposes.get(order.intent_id) is IntentPurpose.TAKE_PROFIT:
        return True
    if order.stop_trigger_price is not None or is_venue_protection(order.kind):
        return False
    unlabeled = order.intent_id not in known_intents
    return unlabeled and order.kind is OrderKind.POST_ONLY_LIMIT


def _stop_shaped(order: Order) -> bool:
    """Only executable venue stop kinds qualify, with or without intent labels."""
    return is_venue_protection(order.kind)


def _fold_hits(hits: tuple[_Hit, ...], *, duplicate: bool) -> _Fold:
    """Sum only fresh OPEN remainders; use the oldest contributing receipt for verification."""
    confirmed = _ZERO
    flags: set[str] = set()
    if duplicate:
        flags.add("duplicate_order_ignored")
    observed: datetime | None = None
    side_valid = False
    geometry_valid = False
    pending = False
    unknown = False
    for hit in hits:
        flags.update(hit.flags)
        observed = _later(observed, hit.observed_at)
        side_valid = side_valid or hit.side_valid
        geometry_valid = geometry_valid or hit.geometry_valid
        if hit.role == "confirmed":
            confirmed += hit.remaining
        elif hit.role == "pending":
            pending = True
        elif hit.role == "unknown":
            unknown = True
    verified = min(
        (
            hit.observed_at
            for hit in hits
            if hit.role == "confirmed" and hit.observed_at is not None
        ),
        default=None,
    )
    return _Fold(
        confirmed,
        pending,
        unknown,
        frozenset(flags),
        observed,
        verified,
        side_valid,
        geometry_valid,
    )


def _ordered_reasons(found: set[str]) -> tuple[str, ...]:
    """Return known reasons in contract order, then any unexpected code sorted."""
    known = tuple(code for code in PROTECTION_REASONS if code in found)
    extra = tuple(sorted(found.difference(PROTECTION_REASONS)))
    return known + extra


def _closing_side(position: Position) -> OrderSide:
    """The order side that reduces this book (sell for a long, buy for a short)."""
    return OrderSide.BUY if position.side is PositionSide.SHORT else OrderSide.SELL


def _book_stop_on_protective_side(position: Position) -> bool | None:
    """Validate stop vs working target, not entry; profitable trailing stops are valid.

    Without a target there is insufficient book-level geometry evidence. An actual
    stop-limit can still establish executable geometry from its limit and trigger.
    Neither a historical entry nor a trail extreme is a current market mark.
    """
    stop = position.stop_price
    target = position.target_price
    if stop <= 0:
        return False
    if target is None:
        return None
    if target <= 0:
        return False
    if position.side is PositionSide.SHORT:
        return stop > target
    return stop < target


def _order_geometry(
    order: Order,
) -> tuple[OrderSide, OrderKind, Decimal | None, Decimal | None, Decimal | None]:
    """Compare executable geometry of duplicate observations without summing quantities."""
    return (order.side, order.kind, order.stop_trigger_price, order.take_profit_price, order.price)


def _unsupported_kind_flags(order: Order, purposes: dict[UUID, IntentPurpose]) -> frozenset[str]:
    """A purpose or stray trigger cannot turn a limit/marketable order into a resting stop."""
    if purposes.get(order.intent_id) in _STOP_PURPOSES or order.stop_trigger_price is not None:
        return frozenset({"unsupported_stop_kind"})
    return frozenset()


def _stop_limit_flags(order: Order, position: Position) -> frozenset[str]:
    """Require an executable limit on the marketable side of the stop trigger.

    This checks the order's geometry, not current venue price or liquidity; an open
    stop-limit is not a guaranteed fill. Missing limit evidence stays unverified.
    """
    limit = order.price
    trigger = order.stop_trigger_price
    if limit is None or trigger is None:
        return frozenset({"stop_geometry_unknown"})
    if limit <= 0 or trigger <= 0:
        return frozenset({"stop_geometry_invalid"})
    valid = limit >= trigger if position.side is PositionSide.SHORT else limit <= trigger
    return frozenset() if valid else frozenset({"stop_geometry_invalid"})


def _observation_flags(
    observed: datetime | None, now: datetime, *, venue_identity: bool
) -> frozenset[str]:
    """Check identity and actual order-state receipt age, never local-row recency."""
    flags: set[str] = set()
    if not venue_identity:
        flags.add("venue_identity_missing")
    if observed is None:
        flags.add("observation_time_unknown")
    elif observed > now:
        flags.add("observation_time_future")
    elif now - observed > LOCAL_EVIDENCE_MAX_AGE:
        flags.add("venue_evidence_stale")
    return frozenset(flags)


def _fold_freshness(fold: _Fold) -> _Freshness:
    """Disclose the worst candidate freshness; a newer partial read cannot refresh others."""
    if "venue_evidence_stale" in fold.flags:
        return "stale"
    if fold.verified_at is None or fold.flags & {
        "observation_time_unknown",
        "observation_time_future",
        "venue_identity_missing",
    }:
        return "unknown"
    return "recent_venue"


def _remaining(order: Order) -> Decimal:
    """Unfilled quantity, floored at zero. Original quantity is not remaining cover."""
    remaining = order.quantity - order.filled_quantity
    return remaining if remaining > 0 else _ZERO


def _later(current: datetime | None, candidate: datetime | None) -> datetime | None:
    """Return the later aware timestamp, ignoring a missing one."""
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _venue_observed(order: Order) -> datetime | None:
    """Return actual receipt provenance, invalidated by UNKNOWN even on inconsistent rows."""
    return None if order.status is OrderStatus.UNKNOWN else _aware(order.venue_observed_at)


def _aware(value: datetime | None) -> datetime | None:
    """Drop missing/naive timestamps rather than presenting them as verified instants."""
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _timestamp(value: datetime | None) -> str | None:
    """ISO-8601 text, or null when the observation is unknown."""
    return None if value is None else value.isoformat()
