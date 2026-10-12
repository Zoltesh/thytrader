"""Strict persisted identity and complete-read classification for CFM report rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.futures_cancel import _owned_order
from thytrader.execution.futures_visibility import visibility_scope, visible_position_comparison
from thytrader.operator.venue_reconciliation_models import FuturesExternalPositionRow

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.operator.venue_reconciliation_models import ForeignOpenOrderRow
    from thytrader.trading.models import DeploymentSnapshot


def managed_order_row(
    row: ForeignOpenOrderRow,
    snapshots: Sequence[DeploymentSnapshot],
) -> bool:
    """Require one exact durable product/client/intent owner, not a UUID-like prefix."""
    owners = [
        (book.deployment.id, order.intent_id)
        for book in snapshots
        for order in book.orders
        if row.client_order_id
        and row.client_order_id == order.client_order_id
        and row.product_id == order.product_id
        and _owned_order(book, order)
        and (order.venue_order_id is None or row.venue_order_id == order.venue_order_id)
    ]
    return len(set(owners)) == 1


def classify_positions(
    rows: tuple[FuturesExternalPositionRow, ...],
    *,
    snapshots: Sequence[DeploymentSnapshot],
    account: FuturesLiveAccount | None,
    complete: bool,
    now: datetime,
) -> tuple[FuturesExternalPositionRow, ...]:
    """Include local products absent at venue; complete fresh emptiness is a zero observation."""
    books = tuple(book for book in snapshots if visibility_scope(book) is not False)
    complete = complete and all(visibility_scope(book) is not None for book in snapshots)
    products = {row.product_id for row in rows}
    missing = tuple(
        FuturesExternalPositionRow(product_id=product, side="unknown", number_of_contracts="0")
        for product in sorted({book.deployment.product_id for book in books} - products)
    )
    return tuple(
        _classify(row, books=snapshots, account=account, complete=complete, now=now)
        for row in (*rows, *missing)
    )


def _classify(
    row: FuturesExternalPositionRow,
    *,
    books: Sequence[DeploymentSnapshot],
    account: FuturesLiveAccount | None,
    complete: bool,
    now: datetime,
) -> FuturesExternalPositionRow:
    """A foreign or incomplete order listing never proves a managed position match."""
    owners = [
        book
        for book in books
        if visibility_scope(book) is not False and book.deployment.product_id == row.product_id
    ]
    fresh = (
        account is not None and account.venue.is_fresh(now) and account.venue.positions is not None
    )
    if not complete or not fresh or len(owners) > 1:
        return row.model_copy(
            update={
                "classification": "unknown",
                "comparison_reason": "INCOMPLETE_OR_AMBIGUOUS_EVIDENCE",
            }
        )
    if not owners:
        return row
    owner = owners[0]
    sample = visible_position_comparison(owner, account, books=books, complete=complete, now=now)
    return row.model_copy(
        update={
            "classification": "unknown"
            if sample is None
            else ("managed_matched" if sample.matches else "managed_mismatch"),
            "deployment_id": owner.deployment.id,
            "book_contracts": None if sample is None else format(sample.book_contracts, "f"),
            "comparison_reason": "POSITION_COMPARISON_UNPROVEN" if sample is None else None,
        }
    )
