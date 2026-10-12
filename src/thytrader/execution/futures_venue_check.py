"""Signed whole-contract reconciliation (ADR 0134 I9), with restart-local confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.execution.exit_guards import active_orders, cancel_pending
from thytrader.trading.fill_ledger import unsettled_fill_evidence
from thytrader.trading.models import OrderStatus, PositionSide, is_venue_protection

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.trading.models import DeploymentSnapshot


@dataclass(frozen=True, slots=True)
class PositionComparison:
    """One comparable position observation, not evidence of operational readiness."""

    observed_at: datetime
    book_contracts: Decimal
    venue_contracts: Decimal

    @property
    def matches(self) -> bool:
        """Whether the signed contract counts agree exactly."""
        return self.book_contracts == self.venue_contracts

    @property
    def detail(self) -> str:
        """A stable, currency-free mismatch explanation."""
        return (
            f"FUTURES_POSITION_MISMATCH: venue {self.venue_contracts} vs "
            f"book {self.book_contracts} contracts."
        )


class PositionConfirmations:
    """Confirm two fresh valid mismatches; restart discards counts, never clears pauses."""

    def __init__(self) -> None:
        """Start without confirmation evidence."""
        self._last: dict[UUID, datetime] = {}
        self._counts: dict[UUID, int] = {}

    def confirm(self, deployment_id: UUID, sample: PositionComparison | None) -> bool:
        """Count distinct observations only; unknown/matching evidence breaks the run."""
        if sample is None:
            self._counts.pop(deployment_id, None)
            return False
        previous = self._last.get(deployment_id)
        if previous is not None and sample.observed_at <= previous:
            return False
        self._last[deployment_id] = sample.observed_at
        count = 0 if sample.matches else self._counts.get(deployment_id, 0) + 1
        self._counts[deployment_id] = count
        return count >= 2


def compare_position(
    snapshot: DeploymentSnapshot, venue: FuturesVenueEvidence | None, *, now: datetime
) -> PositionComparison | None:
    """Compare only quiescent transactions with complete, sufficiently old fill economics.

    A confirmed OPEN, unfilled resting protection is not an in-flight transaction. Pending,
    UNKNOWN, cancel-pending, partially filled, or unobserved protection still invalidates
    comparison. Otherwise permanent protection would disable I9 for the entire holding period.
    """
    if (
        not snapshot.accounting_complete
        or venue is None
        or not venue.is_fresh(now)
        or venue.positions is None
    ):
        return None
    if unsettled_fill_evidence(snapshot) or not _orders_quiescent(snapshot, now):
        return None
    # A future fill stays unknown even at datetime.max; never construct fill + grace.
    if snapshot.fills and venue.observed_at - max(f.filled_at for f in snapshot.fills) <= timedelta(
        seconds=5
    ):
        return None
    product_id = snapshot.deployment.product_id
    size = venue.contract_sizes.get(product_id)
    book = signed_book_contracts(snapshot, size)
    if book is None:
        return None
    return PositionComparison(venue.observed_at, book, venue.positions.get(product_id, Decimal(0)))


def signed_book_contracts(snapshot: DeploymentSnapshot, size: Decimal | None) -> Decimal | None:
    """Convert the position projection exactly at the venue's current catalog size."""
    if size is None or not size.is_finite() or size <= 0:
        return None
    position = snapshot.position
    if position is None:
        return Decimal(0)
    quantity = position.quantity
    if not quantity.is_finite() or quantity <= 0:
        return None
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            count = quantity / size
            if not count.is_finite() or count != count.to_integral_value() or count <= 0:
                return None
            return count.copy_negate() if position.side is PositionSide.SHORT else count
    except DecimalException:
        return None


def _orders_quiescent(snapshot: DeploymentSnapshot, now: datetime) -> bool:
    """Only freshly observed, unfilled resting protection can coexist with comparison."""
    represented = {order.intent_id for order in snapshot.orders}
    if any(
        intent.status in {OrderStatus.PENDING, OrderStatus.UNKNOWN} and intent.id not in represented
        for intent in snapshot.intents
    ):
        return False
    for order in active_orders(snapshot):
        observed = order.venue_observed_at
        if (
            not is_venue_protection(order.kind)
            or order.status is not OrderStatus.OPEN
            or cancel_pending(order)
            or order.filled_quantity != 0
            or observed is None
            or observed > now
            or now - observed > timedelta(seconds=180)
        ):
            return False
    return True
