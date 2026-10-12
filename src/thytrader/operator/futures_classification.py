"""Strict persisted identity and complete-read classification for CFM report rows."""

from __future__ import annotations

from decimal import DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.execution.futures_cancel import _owned_order
from thytrader.execution.futures_live import managed_live_futures
from thytrader.execution.futures_visibility import visible_position_comparison
from thytrader.operator.venue_reconciliation_models import FuturesExternalPositionRow
from thytrader.trading.futures_repair import managed_signed_base

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
    books = tuple(book for book in snapshots if managed_live_futures(book))
    products = {row.product_id for row in rows}
    missing = tuple(
        FuturesExternalPositionRow(product_id=product, side="unknown", number_of_contracts="0")
        for product in sorted({book.deployment.product_id for book in books} - products)
    )
    return tuple(
        _classify(row, books=books, account=account, complete=complete, now=now)
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
    owners = [book for book in books if book.deployment.product_id == row.product_id]
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
    sample = None if account is None else visible_position_comparison(owner, account, now=now)
    external = None if account is None else account.venue.external_order_products
    managed = managed_signed_base(owner)
    size = None if account is None else account.venue.contract_sizes.get(row.product_id)
    if sample is not None and managed is not None and size is not None:
        try:
            with localcontext() as context:
                context.traps[Inexact] = True
                if managed / size != sample.book_contracts:
                    sample = None
        except DecimalException:
            sample = None
    else:
        sample = None
    if external is None or row.product_id in external:
        sample = None
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
