"""Exact per-product reconstruction of UTC opening cash and inventory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from thytrader.execution.day_open import DailyOpeningEvidence, MidnightMark
from thytrader.execution.models import (
    OrderSide,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.models import DeploymentSnapshot, Fill, Order

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class OpeningReplay:
    """Validated day cash movement and separate signed midnight product quantities."""

    day_cash_change: Decimal
    midnight_quantities: dict[str, Decimal]
    fills_fingerprint: str


def utc_day_start(moment: datetime) -> datetime:
    """Return the aware UTC midnight containing an observation."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def opening_replay(snapshot: DeploymentSnapshot, *, as_of: datetime) -> OpeningReplay | None:
    """Prove applied fill cash and current inventory before reconstructing an opening.

    A product overlay, orphan/unapplied fill, future fill, or contradictory cash/base
    projection is incomplete. Signed BTC and ETH quantities never cancel each other.
    """
    if not snapshot.accounting_complete or not _orders_covered(snapshot, as_of=as_of):
        return None
    orders = {order.id: order for order in snapshot.orders}
    current: dict[str, Decimal] = {}
    midnight: dict[str, Decimal] = {}
    cash_change = _ZERO
    day_cash_change = _ZERO
    records: list[tuple[str, ...]] = []
    day_start = utc_day_start(as_of)
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if order is None or not _fill_economics_qualified(fill, order, as_of=as_of):
            return None
        product = resolved_product_id(order.product_id, snapshot.deployment)
        quantity = fill.quantity if order.side is OrderSide.BUY else -fill.quantity
        delta = -quantity * fill.price - fill.fee
        current[product] = current.get(product, _ZERO) + quantity
        cash_change += delta
        if fill.filled_at < day_start:
            midnight[product] = midnight.get(product, _ZERO) + quantity
        else:
            day_cash_change += delta
        records.append(
            (
                str(fill.id),
                str(fill.order_id),
                fill.venue_fill_id,
                product,
                order.side.value,
                str(fill.quantity),
                str(fill.price),
                str(fill.fee),
                fill.filled_at.isoformat(),
            )
        )
    if not _projection_matches(snapshot, current, cash_change):
        return None
    fingerprint = sha256(json.dumps(sorted(records), separators=(",", ":")).encode()).hexdigest()
    return OpeningReplay(day_cash_change, midnight, "sha256:" + fingerprint)


def _orders_covered(snapshot: DeploymentSnapshot, *, as_of: datetime) -> bool:
    """Filled order quantities require matching complete fill records, including closed lots."""
    quantities: dict[str, Decimal] = {}
    identities: set[str] = set()
    orders = {order.id: order for order in snapshot.orders}
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if (
            fill.venue_fill_id in identities
            or order is None
            or not _fill_economics_qualified(fill, order, as_of=as_of)
        ):
            return False
        identities.add(fill.venue_fill_id)
        key = str(fill.order_id)
        quantities[key] = quantities.get(key, _ZERO) + fill.quantity
    return all(
        order.deployment_id == snapshot.deployment.id
        and isinstance(order.filled_quantity, Decimal)
        and order.filled_quantity.is_finite()
        and quantities.get(str(order.id), _ZERO) == order.filled_quantity
        for order in snapshot.orders
    )


def _fill_economics_qualified(fill: Fill, order: Order, *, as_of: datetime) -> bool:
    """Reject corrupt, inexact, future, or unprojected economics before arithmetic."""
    numbers = (fill.quantity, fill.price, fill.fee)
    if not all(isinstance(value, Decimal) and value.is_finite() for value in numbers):
        return False
    return (
        fill.deployment_id == order.deployment_id
        and fill.quantity > 0
        and fill.price > 0
        and fill.fee >= 0
        and fill.economics_applied_at is not None
        and fill.filled_at.tzinfo is not None
        and as_of.tzinfo is not None
        and fill.filled_at <= as_of
    )


def _projection_matches(
    snapshot: DeploymentSnapshot, quantities: dict[str, Decimal], cash_change: Decimal
) -> bool:
    """Recorded initial cash and per-product positions must agree with applied economics."""
    projected: dict[str, Decimal] = {}
    for position in snapshot_positions(snapshot):
        product = resolved_product_id(position.product_id, snapshot.deployment)
        signed = position.quantity if position.side is PositionSide.LONG else -position.quantity
        projected[product] = projected.get(product, _ZERO) + signed
    if {p: q for p, q in quantities.items() if q} != {p: q for p, q in projected.items() if q}:
        return False
    initial = snapshot.deployment.initial_equity
    if initial is None:
        initial = snapshot.deployment.paper_starting_cash
    return initial is None or snapshot.deployment.cash == initial + cash_change


def reconstruct_day_open(
    snapshot: DeploymentSnapshot, *, as_of: datetime, marks: Sequence[MidnightMark] = ()
) -> DailyOpeningEvidence | None:
    """Derive genuine midnight equity, reusing only qualified recorded midnight marks.

    Legacy utc_day_open_* fields are deliberately ignored. Flat midnight inventory
    needs no market price. Current cash minus applied day movements gives opening
    cash; actual closed midnight prices value each nonzero overnight product.
    """
    replay = opening_replay(snapshot, as_of=as_of)
    if replay is None:
        return None
    day_start = utc_day_start(as_of)
    stored = snapshot.deployment.risk_day_open_evidence
    candidates = (*(() if stored is None else stored.marks), *marks)
    available = {mark.product_id: mark for mark in candidates if mark.closes_at == day_start}
    midnight_marks: list[MidnightMark] = []
    equity = snapshot.deployment.cash - replay.day_cash_change
    for product, quantity in sorted(replay.midnight_quantities.items()):
        if quantity == 0:
            continue
        mark = available.get(product)
        if mark is None:
            return None
        equity += quantity * mark.price
        midnight_marks.append(mark)
    return DailyOpeningEvidence(
        day_start=day_start,
        equity=equity,
        fills_fingerprint=replay.fills_fingerprint,
        marks=tuple(midnight_marks),
    )
