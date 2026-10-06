"""Observable per-product protection status from a deployment snapshot.

HTTP and operator reports classify live cover from confirmed open stop orders whose
closing side, remaining quantity, and stop geometry match the book. A take-profit
alone is not cover. Pending and unknown orders are not confirmed cover. Parent
stop/target geometry is never coverage, and an attached child does not bypass those
checks. An open paper book stays ``covered`` because the worker enforces its stop
synthetically, but the evidence says that cover is worker-dependent rather than a
venue-resting order (ADR 0098, ADR 0112).

``position_state`` and ``exit_in_flight`` (ADR 0097) split the worker's internal
``pending_exit`` phase, which is set as soon as any exit order works, into an open
protected book and a book whose exit is actually being sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - evidence timestamps are compared and formatted
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID  # noqa: TC003 - intent maps are keyed at runtime

from pydantic import BaseModel, ConfigDict, Field

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
_STATUS_RANK = {OrderStatus.OPEN: 0, OrderStatus.PENDING: 1, OrderStatus.UNKNOWN: 2}
PROTECTION_REASONS: tuple[str, ...] = (
    "flat",
    "synthetic_worker_dependent",
    "venue_stop_resting",
    "duplicate_order_ignored",
    "take_profit_only",
    "closing_side_mismatch",
    "stop_price_mismatch",
    "stale_bracket",
    "stop_geometry_invalid",
    "stop_quantity_short",
    "partial_stop_quantity",
    "pending_not_confirmed",
    "unknown_not_confirmed",
    "no_resting_stop",
)
"""Stable reason codes for protection evidence, in display order."""


class ProtectionStatus(StrEnum):
    """Whether one product book currently shows confirmed exit cover."""

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

    ``open_protected`` is an open book whose exit cover is confirmed (a matching venue
    stop, or the paper synthetic stop). ``exiting`` is a book whose exit is in flight.
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
    """Quantitative cover for one book. Null times mean the observation is unknown."""

    status: ProtectionStatus
    required_quantity: Decimal
    covered_quantity: Decimal
    uncovered_quantity: Decimal
    stop_side: OrderSide | None
    stop_side_valid: bool
    stop_geometry_valid: bool
    mechanism: ProtectionMechanism
    venue_resting: bool
    worker_dependent: bool
    observed_at: datetime | None
    verified_at: datetime | None
    reasons: tuple[str, ...]


class ProtectionEvidenceResponse(BaseModel):
    """Strict public protection evidence. Quantities are exact decimal strings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_quantity: str
    covered_quantity: str
    uncovered_quantity: str
    stop_side: str | None = Field(description="Closing side the stop must use, or null when flat.")
    stop_side_valid: bool
    stop_geometry_valid: bool
    mechanism: Literal["venue", "synthetic", "none", "unverified"]
    venue_resting: bool = Field(
        description="True only when a confirmed open venue stop contributed."
    )
    worker_dependent: bool = Field(
        description="True for the paper synthetic stop. That cover is not a venue order."
    )
    observed_at: str | None = Field(
        description="Latest persisted update of an inspected active order, or null when unknown."
    )
    verified_at: str | None = Field(
        description="Latest persisted update of a confirmed open stop, or null when unknown."
    )
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Hit:
    """One active order's contribution to cover, after identity dedupe."""

    remaining: Decimal
    role: _HitRole
    flags: frozenset[str]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class _Fold:
    """Book-level totals after unique orders are classified."""

    confirmed: Decimal
    pending: bool
    unknown: bool
    flags: frozenset[str]
    observed_at: datetime | None
    verified_at: datetime | None


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
    stop synthetically on every closed bar. Live books map ``protection_status``. A
    take-profit alone is not covered, so it is ``open_unprotected``.
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


def book_protection_evidence(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
) -> ProtectionEvidence:
    """Return quantitative cover for one book without inventing prices or timestamps.

    Live cover counts only unique open stop orders on the closing side whose remaining
    quantity and stop geometry match the book. The same attached child is counted once.
    Pending and unknown orders never add confirmed quantity. Paper cover is the worker's
    synthetic stop and is labeled as such.
    """
    if position is None or position.quantity <= 0:
        return _flat_evidence()
    if snapshot.deployment.mode is DeploymentMode.PAPER:
        return _paper_evidence(position)
    return _live_evidence(snapshot, product_id, position)


def book_protection_status(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position | None,
) -> ProtectionStatus:
    """Classify protection for one product book from persisted orders and intents.

    Paper books are ``covered`` whether or not a take-profit rests (ADR 0098). Live
    ``covered`` requires a confirmed open stop of sufficient remaining quantity and valid
    geometry. A take-profit alone, a pending stop, and an unknown stop are not covered.
    """
    return book_protection_evidence(snapshot, product_id=product_id, position=position).status


def protection_evidence_response(evidence: ProtectionEvidence) -> ProtectionEvidenceResponse:
    """Serialize one evidence value with exact decimal strings and unknown times as null."""
    return ProtectionEvidenceResponse(
        required_quantity=format(evidence.required_quantity, "f"),
        covered_quantity=format(evidence.covered_quantity, "f"),
        uncovered_quantity=format(evidence.uncovered_quantity, "f"),
        stop_side=None if evidence.stop_side is None else evidence.stop_side.value,
        stop_side_valid=evidence.stop_side_valid,
        stop_geometry_valid=evidence.stop_geometry_valid,
        mechanism=evidence.mechanism.value,
        venue_resting=evidence.venue_resting,
        worker_dependent=evidence.worker_dependent,
        observed_at=_timestamp(evidence.observed_at),
        verified_at=_timestamp(evidence.verified_at),
        reasons=evidence.reasons,
    )


def working_order_count(orders: tuple[Order, ...]) -> int:
    """Count orders that are still in the reconcile watch set."""
    return sum(1 for order in orders if order.status in _WORKING_STATUSES)


def _flat_evidence() -> ProtectionEvidence:
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
        reasons=("flat",),
    )


def _paper_evidence(position: Position) -> ProtectionEvidence:
    """Report the worker synthetic stop without calling it a venue-resting order."""
    geometry_ok = _book_stop_on_protective_side(position)
    reasons = ["synthetic_worker_dependent"]
    if not geometry_ok:
        reasons.append("stop_geometry_invalid")
    return ProtectionEvidence(
        status=ProtectionStatus.COVERED,
        required_quantity=position.quantity,
        covered_quantity=position.quantity,
        uncovered_quantity=_ZERO,
        stop_side=_closing_side(position),
        stop_side_valid=True,
        stop_geometry_valid=geometry_ok,
        mechanism=ProtectionMechanism.SYNTHETIC,
        venue_resting=False,
        worker_dependent=True,
        observed_at=None,
        verified_at=None,
        reasons=_ordered_reasons(set(reasons)),
    )


def _live_evidence(
    snapshot: DeploymentSnapshot, product_id: str, position: Position
) -> ProtectionEvidence:
    """Classify a live book from unique active orders and the book's working stop."""
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
        )
        for order in orders
    )
    fold = _fold_hits(hits, duplicate=duplicate)
    return _evidence_from_fold(position, closing, book_ok, fold)


def _evidence_from_fold(
    position: Position,
    closing: OrderSide,
    book_ok: bool,
    fold: _Fold,
) -> ProtectionEvidence:
    """Turn folded order hits into status, quantities, and reasons."""
    required = position.quantity
    covered = fold.confirmed if fold.confirmed < required else required
    uncovered = required - covered
    status = _live_status(covered, required, fold)
    confirmed_geometry = book_ok and fold.confirmed > _ZERO
    return ProtectionEvidence(
        status=status,
        required_quantity=required,
        covered_quantity=covered,
        uncovered_quantity=uncovered,
        stop_side=closing,
        stop_side_valid=confirmed_geometry,
        stop_geometry_valid=confirmed_geometry,
        mechanism=_live_mechanism(status, fold),
        venue_resting=fold.confirmed > _ZERO,
        worker_dependent=False,
        observed_at=fold.observed_at,
        verified_at=fold.verified_at if fold.confirmed > _ZERO else None,
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
    """Return active product orders, counting each venue child once."""
    active = tuple(
        order
        for order in snapshot.orders
        if order.status in _ACTIVE_STATUSES
        and resolved_product_id(order.product_id, snapshot.deployment) == product_id
    )
    chosen: dict[str, Order] = {}
    duplicate = False
    for order in active:
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
    """Keep the confirmed row, and the smaller remainder when status ties."""
    left_rank = _STATUS_RANK.get(left.status, 9)
    right_rank = _STATUS_RANK.get(right.status, 9)
    if left_rank != right_rank:
        return left if left_rank < right_rank else right
    if _remaining(left) <= _remaining(right):
        return left
    return right


def _classify_order(
    order: Order,
    position: Position,
    *,
    closing: OrderSide,
    book_ok: bool,
    purposes: dict[UUID, IntentPurpose],
    known_intents: frozenset[UUID],
) -> _Hit:
    """Classify one active order. Confirmed cover requires an open matching stop."""
    updated = _aware(order.updated_at)
    if order.side is not closing:
        flags = _wrong_side_flags(order, purposes)
        return _Hit(_ZERO, "ignored", flags, updated)
    if _take_profit_only(order, purposes, known_intents):
        return _Hit(_ZERO, "ignored", frozenset({"take_profit_only"}), updated)
    if not _stop_shaped(order, purposes, known_intents):
        return _Hit(_ZERO, "ignored", frozenset(), updated)
    geometry = _geometry_flags(order, position, book_ok=book_ok)
    if geometry:
        return _Hit(_ZERO, _unreadable_role(order, geometry), geometry, updated)
    remaining = _remaining(order)
    if remaining <= 0:
        return _Hit(_ZERO, "ignored", frozenset({"stop_quantity_short"}), updated)
    return _Hit(remaining, _open_role(order), frozenset(), updated)


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


def _geometry_flags(order: Order, position: Position, *, book_ok: bool) -> frozenset[str]:
    """Return why this stop does not match the book, or empty when it does."""
    if not book_ok:
        return frozenset({"stop_geometry_invalid"})
    if order.stop_trigger_price is None:
        return _missing_trigger_flags(order)
    if order.stop_trigger_price != position.stop_price:
        return _price_mismatch_flags(order)
    if not _target_matches(order, position):
        return frozenset({"stale_bracket"})
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


def _stop_shaped(
    order: Order,
    purposes: dict[UUID, IntentPurpose],
    known_intents: frozenset[UUID],
) -> bool:
    """True for a venue stop, a stop/bracket intent, or an unlabeled summary stop trigger."""
    if is_venue_protection(order.kind):
        return True
    if purposes.get(order.intent_id) in _STOP_PURPOSES:
        return True
    return order.intent_id not in known_intents and order.stop_trigger_price is not None


def _fold_hits(hits: tuple[_Hit, ...], *, duplicate: bool) -> _Fold:
    """Sum unique confirmed remainders and keep the latest real timestamps."""
    confirmed = _ZERO
    flags: set[str] = set()
    if duplicate:
        flags.add("duplicate_order_ignored")
    observed: datetime | None = None
    verified: datetime | None = None
    pending = False
    unknown = False
    for hit in hits:
        flags.update(hit.flags)
        observed = _later(observed, hit.updated_at)
        if hit.role == "confirmed":
            confirmed += hit.remaining
            verified = _later(verified, hit.updated_at)
        elif hit.role == "pending":
            pending = True
        elif hit.role == "unknown":
            unknown = True
    return _Fold(confirmed, pending, unknown, frozenset(flags), observed, verified)


def _ordered_reasons(found: set[str]) -> tuple[str, ...]:
    """Return known reasons in contract order, then any unexpected code sorted."""
    known = tuple(code for code in PROTECTION_REASONS if code in found)
    extra = tuple(sorted(found.difference(PROTECTION_REASONS)))
    return known + extra


def _closing_side(position: Position) -> OrderSide:
    """The order side that reduces this book (sell for a long, buy for a short)."""
    return OrderSide.BUY if position.side is PositionSide.SHORT else OrderSide.SELL


def _book_stop_on_protective_side(position: Position) -> bool:
    """True when the book's stop is on the protective side of its entry."""
    entry = position.entry_price
    stop = position.stop_price
    if entry <= 0 or stop <= 0:
        return False
    if position.side is PositionSide.SHORT:
        return stop > entry
    return stop < entry


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


def _aware(value: datetime) -> datetime | None:
    """Drop a naive timestamp rather than presenting it as a verified instant."""
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value


def _timestamp(value: datetime | None) -> str | None:
    """ISO-8601 text, or null when the observation is unknown."""
    return None if value is None else value.isoformat()
