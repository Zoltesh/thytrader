"""Complete economic evidence for synthetic protection-order observation fixtures."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from thytrader.execution.models import DeploymentSnapshot, Fill, OrderStatus


def settled_snapshot(snapshot: DeploymentSnapshot) -> DeploymentSnapshot:
    """Make toy, already-projected executions explicit before testing stop provenance.

    These fixtures test identity/geometry, not fill ingestion. Supplied positions already
    reflect execution; each toy local observation needs its corresponding applied fill.
    Integration regressions deliberately do not use this fixture helper.
    """
    fills = [replace(fill, economics_applied_at=fill.filled_at) for fill in snapshot.fills]
    for order in snapshot.orders:
        executed = (
            max(order.quantity, order.filled_quantity)
            if order.status is OrderStatus.FILLED
            else order.filled_quantity
        )
        recorded = sum((fill.quantity for fill in fills if fill.order_id == order.id), Decimal(0))
        if executed <= recorded:
            continue
        fills.append(
            Fill(
                id=uuid4(),
                deployment_id=order.deployment_id,
                order_id=order.id,
                venue_fill_id=f"fixture-execution-{order.id}",
                price=order.price or order.stop_trigger_price or Decimal("100"),
                quantity=executed - recorded,
                fee=Decimal(0),
                filled_at=order.created_at,
                economics_applied_at=order.created_at,
            )
        )
    return replace(snapshot, fills=tuple(fills))
