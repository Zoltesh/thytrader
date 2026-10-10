"""Name exactly why one book has no daily-loss evidence (operator diagnosis, ADR 0130).

The daily-loss breaker (``risk.breakers``) needs a book's UTC-day equity change, which
``risk.opening_accounting`` proves from applied fills. When that proof fails the breaker only
says the evidence is missing. This module replays the same rules and names every failed one,
for example ``order X FILLED with filled_quantity 0 but fills sum 0.00014174``, so an
operator can repair the record that blocks the fleet. It never decides admission: the gate's
own functions do, and this only explains a ``None`` they already returned.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.risk.opening_accounting import (
    _fill_economics_qualified,
    _funding_qualified,
    opening_replay,
    utc_day_start,
)
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    OrderSide,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection import missing_occupied_inventory_products

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from thytrader.trading.models import DeploymentSnapshot

_ZERO = Decimal(0)
_MAX_GAPS = 6


def daily_evidence_gaps(
    snapshot: DeploymentSnapshot, *, as_of: datetime, marks: Mapping[str, Decimal]
) -> tuple[str, ...]:
    """Every rule that keeps this book's daily-loss evidence unknown, most basic first."""
    gaps = [*accounting_gaps(snapshot), *_mark_gaps(snapshot, marks)]
    if gaps:
        return tuple(gaps[:_MAX_GAPS])
    if utc_day_start(snapshot.deployment.created_at) == utc_day_start(as_of):
        if snapshot.deployment.initial_equity is None and (
            snapshot.deployment.paper_starting_cash is None
        ):
            return ("the book started today but has no recorded opening balance",)
        return ()
    gaps = [*_order_gaps(snapshot, as_of=as_of), *_funding_gaps(snapshot, as_of=as_of)]
    if not gaps:
        gaps = list(_projection_gaps(snapshot, as_of=as_of))
    return tuple(gaps[:_MAX_GAPS])


def accounting_gaps(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """The unresolved-accounting rules the gate applies, named."""
    gaps: list[str] = []
    if not snapshot.accounting_complete:
        gaps.append("the accounting snapshot omitted sibling economics")
    if unsettled_fill_evidence(snapshot):
        gaps.append("recorded executions exceed applied fill economics (unsettled fills)")
    unprojected = unprojected_inventory_products(snapshot)
    if unprojected:
        gaps.append(f"applied inventory not in the position projection: {', '.join(unprojected)}")
    missing = missing_occupied_inventory_products(snapshot)
    if missing:
        gaps.append(f"occupied runtimes without inventory: {', '.join(missing)}")
    return tuple(gaps)


def _mark_gaps(snapshot: DeploymentSnapshot, marks: Mapping[str, Decimal]) -> tuple[str, ...]:
    """Open inventory without a last-close mark, or a ledger that cannot be marked."""
    unmarked = sorted(
        {
            resolved_product_id(position.product_id, snapshot.deployment)
            for position in snapshot_positions(snapshot)
            if marks.get(resolved_product_id(position.product_id, snapshot.deployment)) is None
        }
    )
    if unmarked:
        return (f"no last-close mark for open inventory in {', '.join(unmarked)}",)
    ledger = ledger_from_snapshot(snapshot, marks=marks)
    if not ledger.mark_complete or ledger.equity is None:
        return ("the ledger equity cannot be marked",)
    return ()


def _order_gaps(snapshot: DeploymentSnapshot, *, as_of: datetime) -> tuple[str, ...]:
    """Fills that do not qualify and orders whose filled quantity the fills do not match."""
    gaps: list[str] = []
    orders = {order.id: order for order in snapshot.orders}
    sums: dict[str, Decimal] = {}
    seen: set[str] = set()
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if fill.venue_fill_id in seen:
            gaps.append(f"fill {fill.id} repeats venue fill id {fill.venue_fill_id}")
        elif order is None:
            gaps.append(f"fill {fill.id} references unknown order {fill.order_id}")
        elif not _fill_economics_qualified(fill, order, as_of=as_of):
            gaps.append(f"fill {fill.id} on order {order.id} has unapplied or invalid economics")
        seen.add(fill.venue_fill_id)
        sums[str(fill.order_id)] = sums.get(str(fill.order_id), _ZERO) + fill.quantity
    for order in snapshot.orders:
        total = sums.get(str(order.id), _ZERO)
        if order.deployment_id != snapshot.deployment.id:
            gaps.append(f"order {order.id} belongs to deployment {order.deployment_id}")
        elif not order.filled_quantity.is_finite() or total != order.filled_quantity:
            gaps.append(
                f"order {order.id} {order.status.value.upper()} with filled_quantity "
                f"{_text(order.filled_quantity)} but fills sum {_text(total)}"
            )
    return tuple(gaps)


def _funding_gaps(snapshot: DeploymentSnapshot, *, as_of: datetime) -> tuple[str, ...]:
    """Applied funding hours that do not belong to this book or lie in the future."""
    return tuple(
        f"funding hour {flow.funding_time.isoformat()} on {flow.product_id} does not qualify"
        for flow in snapshot.funding
        if not _funding_qualified(flow, snapshot, as_of=as_of)
    )


def _projection_gaps(snapshot: DeploymentSnapshot, *, as_of: datetime) -> tuple[str, ...]:
    """Fill quantities or cash that disagree with positions, or a missing midnight mark."""
    replay = opening_replay(snapshot, as_of=as_of)
    if replay is None:
        return _cash_or_position_gaps(snapshot)
    day_start = utc_day_start(as_of)
    stored = snapshot.deployment.risk_day_open_evidence
    marked = {
        mark.product_id
        for mark in (() if stored is None else stored.marks)
        if mark.closes_at == day_start
    }
    missing = sorted(
        f"{product} (overnight quantity {_text(quantity)})"
        for product, quantity in replay.midnight_quantities.items()
        if quantity != 0 and product not in marked
    )
    if missing:
        return (f"no closed UTC-midnight mark for {', '.join(missing)}",)
    return ("daily-loss evidence is unavailable for an unclassified reason",)


def _cash_or_position_gaps(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """Name the projection mismatch ``opening_replay`` rejected."""
    quantities: dict[str, Decimal] = {}
    cash_change = _ZERO
    orders = {order.id: order for order in snapshot.orders}
    for fill in snapshot.fills:
        order = orders[fill.order_id]
        product = resolved_product_id(order.product_id, snapshot.deployment)
        signed = fill.quantity if order.side is OrderSide.BUY else -fill.quantity
        quantities[product] = quantities.get(product, _ZERO) + signed
        cash_change += -signed * fill.price - fill.fee
    cash_change += sum((flow.amount for flow in snapshot.funding), _ZERO)
    projected: dict[str, Decimal] = {}
    for position in snapshot_positions(snapshot):
        product = resolved_product_id(position.product_id, snapshot.deployment)
        signed = position.quantity if position.side is PositionSide.LONG else -position.quantity
        projected[product] = projected.get(product, _ZERO) + signed
    gaps = [
        f"applied fills net {_text(quantities.get(product, _ZERO))} {product} but positions "
        f"hold {_text(projected.get(product, _ZERO))}"
        for product in sorted(set(quantities) | set(projected))
        if quantities.get(product, _ZERO) != projected.get(product, _ZERO)
    ]
    deployment = snapshot.deployment
    initial = deployment.initial_equity
    if initial is None:
        initial = deployment.paper_starting_cash
    if initial is not None and deployment.cash != initial + cash_change:
        gaps.append(
            f"cash {_text(deployment.cash)} differs from opening {_text(initial)} plus applied "
            f"fill cash {_text(cash_change)}"
        )
    return tuple(gaps) or ("daily-loss evidence is unavailable for an unclassified reason",)


def _text(value: Decimal) -> str:
    """Exact plain decimal text (``0`` stays ``0``)."""
    return format(value, "f")
