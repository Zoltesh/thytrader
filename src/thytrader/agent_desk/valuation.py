"""What a desk book is worth to its desk, from the book's own fills (ADR 0147, 0148).

A paper book's cash is its allocation, but a live book's cash is the venue balance it shares
with every other live book. So a desk values each book the same way in both modes: the
allocation, plus sale proceeds, minus purchase cost, minus fees, plus the held quantity at
the mark. A flat book's value is what settlement returns to the pool.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.trading.models import OrderSide

if TYPE_CHECKING:
    from thytrader.agent_desk.models import DeskBook
    from thytrader.trading.models import DeploymentSnapshot


def net_quantity(snapshot: DeploymentSnapshot) -> Decimal | None:
    """Bought minus sold base quantity, or None when a fill's order is unknown."""
    sides = {order.id: order.side for order in snapshot.orders}
    quantity = Decimal(0)
    for fill in snapshot.fills:
        side = sides.get(fill.order_id)
        if side is None:
            return None
        quantity += fill.quantity if side is OrderSide.BUY else -fill.quantity
    return quantity


def book_value(
    book: DeskBook, snapshot: DeploymentSnapshot, mark: Decimal | None
) -> Decimal | None:
    """The book's worth to the desk; None while held inventory has no mark or fills are unknown."""
    sides = {order.id: order.side for order in snapshot.orders}
    value = book.allocated_quote
    quantity = Decimal(0)
    for fill in snapshot.fills:
        side = sides.get(fill.order_id)
        if side is None:
            return None
        notional = fill.price * fill.quantity
        if side is OrderSide.BUY:
            value -= notional
            quantity += fill.quantity
        else:
            value += notional
            quantity -= fill.quantity
        value -= fill.fee
    if quantity == 0:
        return value
    if mark is None:
        return None
    return value + quantity * mark
