"""Pure guards that order live exits, cancels, and protective re-submits safely.

These helpers read one deployment snapshot and never touch a broker or store. The loop
uses them to decide whether a cycle may rest protection, must exit first, has to wait
for a venue cancel or exit fill, or should hold back a protective submit the venue
already rejected deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from thytrader.execution.broker import CANCEL_PENDING_REASON
from thytrader.execution.fill_ledger import applied_fill_quantity
from thytrader.execution.models import (
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderStatus,
    RuntimePhase,
)
from thytrader.execution.reconcile import FILLED_WITHOUT_REST_FILLS_DETAIL

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from thytrader.execution.models import DeploymentSnapshot, Order, Position

_ACTIVE = frozenset({OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN})
_EXIT_PURPOSES = frozenset({IntentPurpose.STOP, IntentPurpose.TIME_EXIT, IntentPurpose.SIGNAL_EXIT})
"""Marketable exits: stop/flatten, time exit, and the ``exits.signal_exit`` rule (ADR 0093)."""
_FLAT_PHASES = frozenset({RuntimePhase.FLAT})

CANCEL_BEFORE_EXIT_DETAIL = "Could not cancel resting orders before a marketable exit."
MARKETABLE_EXIT_UNCONFIRMED_DETAIL = "Marketable exit was not confirmed filled."
BRACKET_REPLACE_CANCEL_DETAIL = (
    "Could not cancel the resting exit before replacing the live bracket."
)
BRACKET_UNCONFIRMED_DETAIL = "Live bracket submit is unconfirmed."
BRACKET_NOT_RESTED_DETAIL = "Live bracket could not be rested on an open position."
PROTECTIVE_REJECTED_PREFIX = "PROTECTIVE_SUBMIT_REJECTED"
"""Prefix of the pause detail written when the venue deterministically rejects a bracket."""

POSITION_FAULT_DETAILS = frozenset(
    {
        CANCEL_BEFORE_EXIT_DETAIL,
        MARKETABLE_EXIT_UNCONFIRMED_DETAIL,
        BRACKET_REPLACE_CANCEL_DETAIL,
        BRACKET_UNCONFIRMED_DETAIL,
        BRACKET_NOT_RESTED_DETAIL,
        FILLED_WITHOUT_REST_FILLS_DETAIL,
    }
)
"""Pause details that describe an open position; they are stale once the book is flat."""

FLAT_AFTER_FAULT_DETAIL = (
    "Position is flat and no orders are working; the earlier protection fault no longer "
    "applies. Review the book and resume when ready."
)

_BACKOFF_BASE = timedelta(minutes=1)
_BACKOFF_CAP = timedelta(minutes=30)
MAX_IDENTICAL_REJECTIONS = 5
"""Consecutive identical rejections after which a submit waits for an operator resume."""


@dataclass(frozen=True, slots=True)
class SubmitRejection:
    """The run of identical venue-rejected protective submits for the current position."""

    label: str
    count: int
    reason: str
    last_rejected_at: datetime

    def retry_at(self) -> datetime:
        """Return when the next identical submit may be attempted (exponential backoff)."""
        delay = min(_BACKOFF_BASE * (2 ** (self.count - 1)), _BACKOFF_CAP)
        return self.last_rejected_at + delay


def flatten_requested(snapshot: DeploymentSnapshot) -> bool:
    """True when the operator asked to flatten; no new protection may be rested."""
    return snapshot.deployment.lifecycle_command is LifecycleCommand.FLATTEN


def cancel_pending(order: Order) -> bool:
    """True when the venue accepted a cancel for this still-active order."""
    return order.status in _ACTIVE and order.reject_reason == CANCEL_PENDING_REASON


def active_orders(snapshot: DeploymentSnapshot) -> tuple[Order, ...]:
    """Return every locally active order on the snapshot."""
    return tuple(order for order in snapshot.orders if order.status in _ACTIVE)


def exit_fill_pending(snapshot: DeploymentSnapshot) -> Order | None:
    """Return a marketable exit the venue reports FILLED whose fills are not applied yet.

    Until its fills are ingested the local position still looks open; resting a bracket
    or sending another exit for it would be rejected for insufficient base.
    """
    exit_ids = {item.id for item in snapshot.intents if item.purpose in _EXIT_PURPOSES}
    for order in snapshot.orders:
        if order.status is not OrderStatus.FILLED or order.kind is not OrderKind.MARKETABLE:
            continue
        if order.intent_id not in exit_ids:
            continue
        covered = order.filled_quantity if order.filled_quantity > 0 else order.quantity
        if applied_fill_quantity(snapshot, order.id) < covered:
            return order
    return None


def bracket_rejection(snapshot: DeploymentSnapshot, position: Position) -> SubmitRejection | None:
    """Return the latest run of rejected live brackets matching this position's geometry.

    The run stops at the newest bracket that was not rejected, or whose quantity, target,
    stop, or reject reason differs, so a ratcheted stop or a new position starts fresh.
    """
    return _rejection_run(
        snapshot,
        label="live bracket" if position.target_price is not None else "live stop-limit",
        purposes=frozenset({IntentPurpose.BRACKET}),
        matches=lambda order: _same_geometry(order, position),
    )


def exit_rejection(snapshot: DeploymentSnapshot, position: Position) -> SubmitRejection | None:
    """Return the latest run of rejected marketable exits for this position's quantity."""
    return _rejection_run(
        snapshot,
        label="marketable exit",
        purposes=_EXIT_PURPOSES,
        matches=lambda order: (
            order.kind is OrderKind.MARKETABLE and order.quantity == position.quantity
        ),
    )


def rejection_latched(
    snapshot: DeploymentSnapshot, rejection: SubmitRejection, *, now: datetime
) -> bool:
    """True while an identical rejected submit must not be sent again yet.

    Retries back off exponentially (1, 2, 4, ... minutes, capped at 30). After
    ``MAX_IDENTICAL_REJECTIONS`` identical rejections the latch holds until the operator
    resumes the book (which clears the rejection detail), then allows one more attempt.
    """
    if now < rejection.retry_at():
        return True
    if rejection.count < MAX_IDENTICAL_REJECTIONS:
        return False
    detail = snapshot.deployment.mismatch_detail or ""
    return detail.startswith(PROTECTIVE_REJECTED_PREFIX)


def rejection_detail(rejection: SubmitRejection) -> str:
    """Render the operator-facing pause reason for a deterministic venue rejection."""
    return (
        f"{PROTECTIVE_REJECTED_PREFIX}: Coinbase rejected the {rejection.label} "
        f"{rejection.count} time(s) ({rejection.reason[:160]}). Identical retries back off "
        "and stop after repeated rejections; check balances and holds on Coinbase, then "
        "resume."
    )


def _rejection_run(
    snapshot: DeploymentSnapshot,
    *,
    label: str,
    purposes: frozenset[IntentPurpose],
    matches: Callable[[Order], bool],
) -> SubmitRejection | None:
    """Count the newest consecutive REJECTED orders sharing purpose, geometry, and reason."""
    intent_ids = {item.id for item in snapshot.intents if item.purpose in purposes}
    candidates = sorted(
        (order for order in snapshot.orders if order.intent_id in intent_ids),
        key=lambda order: order.created_at,
        reverse=True,
    )
    count = 0
    reason: str | None = None
    last_at: datetime | None = None
    for order in candidates:
        if order.status is not OrderStatus.REJECTED or not matches(order):
            break
        current = order.reject_reason or "rejected"
        if reason is not None and current != reason:
            break
        reason = current
        last_at = last_at or order.updated_at
        count += 1
    if count == 0 or reason is None or last_at is None:
        return None
    return SubmitRejection(label=label, count=count, reason=reason, last_rejected_at=last_at)


def flat_and_idle(snapshot: DeploymentSnapshot) -> bool:
    """True when the book holds no position, no in-market phase, and no working order."""
    if snapshot.position is not None or snapshot.positions:
        return False
    if snapshot.deployment.phase not in _FLAT_PHASES:
        return False
    if any(runtime.phase not in _FLAT_PHASES for runtime in snapshot.instrument_runtimes):
        return False
    return not active_orders(snapshot)


def stale_position_fault(detail: str | None) -> bool:
    """True when a pause detail only describes an open position's protection or exit."""
    if detail is None:
        return False
    return detail in POSITION_FAULT_DETAILS or detail.startswith(PROTECTIVE_REJECTED_PREFIX)


def _same_geometry(order: Order, position: Position) -> bool:
    """Whether one protective order covers exactly this position's quantity, target, and stop.

    A position without a take-profit (ADR 0090) is covered by a stop-limit, whose limit
    price is derived from the stop rather than being a target.
    """
    if order.quantity != position.quantity or order.stop_trigger_price != position.stop_price:
        return False
    if position.target_price is None:
        return order.kind is OrderKind.STOP_LIMIT
    return order.price == position.target_price
