"""UTC-day fill evidence for flat books whose opening mark was not recorded."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.execution.models import OrderSide, resolved_product_id

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.models import DeploymentSnapshot


def flat_day_fill_pnl(snapshot: DeploymentSnapshot, *, since: datetime) -> Decimal | None:
    """Return fee-inclusive day cash change only when midnight and current inventory are flat.

    Replay separate base quantities for each product, never a BTC/ETH combined lot. A
    closing fill on an overnight position needs an opening mark to establish day equity
    change; its lifetime realized PnL is not a substitute. No fills today on a currently
    flat book means zero today, even when yesterday's equity baseline is stale.
    """
    if (
        not snapshot.accounting_complete
        or unsettled_fill_evidence(snapshot)
        or unprojected_inventory_products(snapshot)
    ):
        return None
    orders = {order.id: order for order in snapshot.orders}
    opening: dict[str, Decimal] = {}
    closing: dict[str, Decimal] = {}
    pnl = Decimal("0")
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if order is None:
            return None
        product = resolved_product_id(order.product_id, snapshot.deployment)
        quantity = fill.quantity if order.side is OrderSide.BUY else -fill.quantity
        closing[product] = closing.get(product, Decimal("0")) + quantity
        if fill.filled_at < since:
            opening[product] = opening.get(product, Decimal("0")) + quantity
        else:
            pnl -= quantity * fill.price + fill.fee
    if any(opening.values()) or any(closing.values()):
        return None
    return pnl
