"""Protection evidence builders for flat, unresolved, paper and live books.

A paper book is covered by the worker's synthetic stop and says so; a live book is
covered only by the folded venue stop orders on the closing side. Unresolved inventory
supplies neither flatness nor executable geometry.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.trading.models import (
    DeploymentSnapshot,
    OrderSide,
    Position,
    PositionSide,
    resolved_product_id,
)
from thytrader.trading.protection_models import (
    _ZERO,
    LOCAL_EVIDENCE_MAX_AGE,
    PROTECTION_REASONS,
    ProtectionEvidence,
    ProtectionMechanism,
    ProtectionStatus,
    _Freshness,
)
from thytrader.trading.protection_orders import (
    _classify_order,
    _cover_identity,
    _deduped_active,
    _Fold,
    _fold_hits,
)

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal


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
