"""Per-order classification of live stop cover for one product book.

Dedupes active orders by venue identity, classifies each as confirmed, pending, unknown
or ignored cover from its side, geometry, kind and venue observation time, and folds
the hits into book-level totals. Only venue order-state reads supply freshness.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - intent maps are keyed at runtime

from thytrader.trading.models import (
    DeploymentSnapshot,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    is_venue_protection,
    resolved_product_id,
)
from thytrader.trading.protection_models import (
    _ACTIVE_STATUSES,
    _STOP_PURPOSES,
    _ZERO,
    LOCAL_EVIDENCE_MAX_AGE,
)

if TYPE_CHECKING:
    from decimal import Decimal

_HitRole = Literal["confirmed", "pending", "unknown", "ignored"]


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
