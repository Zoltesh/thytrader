"""Exact ownership proof and atomic accounting of explicit futures excess reductions.

The repair intent uses the existing LIQUIDATION purpose and a reserved idempotency prefix.
It closes fill-proven inventory outside the projected book, so its cash/fees are applied
without shrinking that projected book a second time. This never adopts or imports inventory.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.models import (
    DeploymentMode,
    IntentPurpose,
    OrderKind,
    OrderSide,
    PositionSide,
)

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.trading.models import DeploymentSnapshot, Fill, Order

EXCESS_REDUCTION_PREFIX = "futures-flip:"


def managed_signed_base(snapshot: DeploymentSnapshot) -> Decimal | None:
    """Prove exact signed inventory from applied fills with durable matching managed identities."""
    orders = {order.id: order for order in snapshot.orders}
    intents = {intent.id: intent for intent in snapshot.intents}
    seen: set[str] = set()
    total = Decimal(0)
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            for fill in snapshot.fills:
                order = orders.get(fill.order_id)
                intent = None if order is None else intents.get(order.intent_id)
                if (
                    order is None
                    or intent is None
                    or fill.economics_applied_at is None
                    or fill.venue_fill_id in seen
                    or not fill.quantity.is_finite()
                    or fill.quantity <= 0
                    or order.client_order_id != intent.client_order_id
                    or not order.client_order_id.startswith(
                        f"{snapshot.deployment.id}:{intent.purpose.value}:"
                    )
                    or order.product_id != snapshot.deployment.product_id
                    or fill.deployment_id != snapshot.deployment.id
                ):
                    return None
                seen.add(fill.venue_fill_id)
                total += (
                    fill.quantity if order.side is OrderSide.BUY else fill.quantity.copy_negate()
                )
    except DecimalException:
        return None
    return total if total.is_finite() else None


def excess_quantity(snapshot: DeploymentSnapshot, managed: Decimal) -> Decimal:
    """The amount outside the intended book; an opposite-side venue is reduced only to flat."""
    position = snapshot.position
    if position is None:
        return managed.copy_abs()
    book = (
        position.quantity.copy_negate()
        if position.side is PositionSide.SHORT
        else position.quantity
    )
    if managed == 0:
        return Decimal(0)
    if book.is_signed() != managed.is_signed():
        return managed.copy_abs()
    return max(Decimal(0), managed.copy_abs() - book.copy_abs())


def project_excess_reduction(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    now: datetime,
) -> tuple[DeploymentSnapshot, Fill] | None:
    """Apply only a validated repair's cash and fee in the existing atomic fill transaction."""
    intent = next((item for item in snapshot.intents if item.id == order.intent_id), None)
    if intent is None or not (intent.idempotency_key or "").startswith(EXCESS_REDUCTION_PREFIX):
        return None
    if (
        snapshot.deployment.mode is not DeploymentMode.LIVE
        or not is_futures_product_id(snapshot.deployment.product_id)
        or intent.purpose is not IntentPurpose.LIQUIDATION
        or order.kind is not OrderKind.MARKETABLE
    ):
        raise ValueError("Invalid futures excess-reduction identity.")
    # The candidate fill may already have been inserted but has not been accounted for.
    prior = replace(snapshot, fills=tuple(item for item in snapshot.fills if item.id != fill.id))
    managed = managed_signed_base(prior)
    if managed is None or managed == 0 or (order.side is OrderSide.BUY) == (managed > 0):
        raise ValueError("Futures excess reduction lacks applied managed-fill ownership proof.")
    with localcontext() as context:
        context.traps[Inexact] = True
        excess = excess_quantity(prior, managed)
        if not fill.quantity.is_finite() or fill.quantity <= 0 or fill.quantity > excess:
            raise ValueError("Futures repair fill exceeds proven managed excess.")
        value = fill.quantity * fill.price
        cash = (
            snapshot.deployment.cash
            + (value if order.side is OrderSide.SELL else -value)
            - fill.fee
        )
        if not cash.is_finite():
            raise ValueError("Futures repair economics are not finite.")
    stamped = replace(fill, economics_applied_at=now)
    fills = (*tuple(item for item in snapshot.fills if item.id != fill.id), stamped)
    return replace(
        snapshot, deployment=replace(snapshot.deployment, cash=cash, updated_at=now), fills=fills
    ), stamped
