"""Last-bar marks and unrealized PnL for open books, for read views only (ADR 0098).

A book's mark is the close of the newest bar its bot evaluated for that product, read
from the per-bar decision journal (ADR 0087). It is the price the worker itself last saw,
so it never needs a venue or market-data call on a read. Gross PnL is signed quantity
times the move from entry price. Verified applied fills establish paid entry fees
allocated to held inventory for net PnL (ADR 0100), excluding future exit fees. Missing
marks or fee evidence stay unknown; nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from thytrader.execution.decision_store import DecisionStoreError
from thytrader.trading.ledger import (
    MAX_POSITION_FEE_FILLS,
    ledger_fills_for_product,
    remaining_position_entry_fees,
)
from thytrader.trading.models import (
    ExecutionStoreError,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.trading.models import DeploymentSnapshot, Position
    from thytrader.trading.store import ExecutionStore

_LOOKAHEAD = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class BookMark:
    """The close of the newest bar a bot evaluated for one product."""

    product_id: str
    price: Decimal
    bar_closes_at: datetime


async def last_bar_marks(
    journal: DecisionJournalStore,
    snapshot: DeploymentSnapshot,
    *,
    now: datetime | None = None,
) -> dict[str, BookMark]:
    """Return a last-bar mark for every open book of ``snapshot`` that has one.

    A journal outage or a decision without a close leaves that book unmarked rather than
    failing the read.
    """
    horizon = (now or datetime.now(UTC)) + _LOOKAHEAD
    marks: dict[str, BookMark] = {}
    for position in snapshot_positions(snapshot):
        product_id = resolved_product_id(position.product_id, snapshot.deployment)
        try:
            decision = await journal.latest_before(snapshot.deployment.id, product_id, horizon)
        except DecisionStoreError:
            continue
        if decision is None or decision.close_price is None:
            continue
        try:
            price = Decimal(decision.close_price)
        except InvalidOperation:
            continue
        marks[product_id] = BookMark(
            product_id=product_id, price=price, bar_closes_at=decision.bar_closes_at
        )
    return marks


async def marks_by_deployment(
    journal: DecisionJournalStore, snapshots: Iterable[DeploymentSnapshot]
) -> dict[UUID, dict[str, BookMark]]:
    """Last-bar marks for the open books of several deployments, keyed by deployment id."""
    now = datetime.now(UTC)
    marks: dict[UUID, dict[str, BookMark]] = {}
    for snapshot in snapshots:
        if not snapshot_positions(snapshot):
            continue
        found = await last_bar_marks(journal, snapshot, now=now)
        if found:
            marks[snapshot.deployment.id] = found
    return marks


def unrealized_pnl(position: Position, mark: Decimal) -> Decimal:
    """Gross unrealized PnL of one book at ``mark``; positive is a gain, before exit fees."""
    return signed_unrealized_pnl(
        quantity=position.quantity, entry_price=position.entry_price, side=position.side, mark=mark
    )


def signed_unrealized_pnl(
    *, quantity: Decimal, entry_price: Decimal, side: PositionSide, mark: Decimal
) -> Decimal:
    """Signed quantity (negative for a short) times the move from ``entry_price`` to ``mark``."""
    signed = -quantity if side is PositionSide.SHORT else quantity
    return signed * (mark - entry_price)


def recorded_position_entry_fees(
    snapshot: DeploymentSnapshot, position: Position
) -> Decimal | None:
    """Read the held inventory's entry fees from an already-loaded full snapshot.

    Only applied fills since this position's entry bar participate. This matches the
    bounded store read and does not turn missing fill evidence into a free entry.
    """
    product_id = resolved_product_id(position.product_id, snapshot.deployment)
    current = replace(
        snapshot,
        fills=tuple(
            fill
            for fill in snapshot.fills
            if fill.economics_applied_at is not None and fill.filled_at >= position.entered_bar
        ),
    )
    fills = ledger_fills_for_product(current, product_id)
    return remaining_position_entry_fees(position, fills[: MAX_POSITION_FEE_FILLS + 1])


async def entry_fees_by_product(
    store: ExecutionStore, snapshot: DeploymentSnapshot, marks: dict[str, BookMark]
) -> dict[str, Decimal | None]:
    """Read bounded entry-fee evidence only for marked open books; outages stay unknown."""
    fees: dict[str, Decimal | None] = {}
    for position in snapshot_positions(snapshot):
        product_id = resolved_product_id(position.product_id, snapshot.deployment)
        if product_id not in marks:
            continue
        try:
            fees[product_id] = await store.get_position_entry_fees(position, product_id=product_id)
        except ExecutionStoreError:
            fees[product_id] = None
    return fees
