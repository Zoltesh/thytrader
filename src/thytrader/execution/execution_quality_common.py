"""Exact sums and fill partitioning shared by execution-quality evidence (ADR 0116).

The round-trip report and the paper/live twin comparison both sum recorded decimals
exactly, classify liquidity only from the recorded order kind, and split a snapshot's
fills into applied evidence, unapplied economics, and orphan rows. Keeping these here
lets both read the same facts without importing each other.
"""

from __future__ import annotations

from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, localcontext
from typing import TYPE_CHECKING

from thytrader.trading.models import DeploymentSnapshot, OrderKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from uuid import UUID

    from thytrader.execution.execution_quality_models import MakerTakerEvidence
    from thytrader.trading.models import Fill, Order


_ZERO = Decimal(0)


def _sum_exact(values: Iterable[Decimal]) -> Decimal:
    """Sum recorded decimals exactly, independent of the ambient Decimal context."""
    materialized = tuple(values)
    if not materialized:
        return _ZERO
    lowest = min(value.adjusted() - len(value.as_tuple().digits) + 1 for value in materialized)
    highest = max(value.adjusted() for value in materialized)
    precision = highest - lowest + len(str(len(materialized))) + 3
    exact = Context(
        prec=precision,
        Emin=-20000,
        Emax=20000,
        traps=[Inexact, InvalidOperation, Overflow],
    )
    with localcontext(exact):
        return sum(materialized, start=_ZERO)


def _liquidity_evidence(order: Order) -> MakerTakerEvidence | None:
    """Classify liquidity only when the recorded order kind proves it."""
    if order.kind is OrderKind.POST_ONLY_LIMIT:
        return "maker"
    if order.kind is OrderKind.MARKETABLE:
        return "taker"
    return None


def _partition_fills(
    snapshot: DeploymentSnapshot, orders: Mapping[UUID, Order]
) -> tuple[tuple[tuple[Fill, Order], ...], int, int]:
    """Split fills into applied evidence, unapplied economics, and orphan rows."""
    applied: list[tuple[Fill, Order]] = []
    unapplied = 0
    orphan = 0
    for fill in sorted(snapshot.fills, key=lambda item: (item.filled_at, item.venue_fill_id)):
        order = orders.get(fill.order_id)
        if order is None:
            orphan += 1
            continue
        if fill.economics_applied_at is None:
            unapplied += 1
            continue
        applied.append((fill, order))
    return tuple(applied), unapplied, orphan
