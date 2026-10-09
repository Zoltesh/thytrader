"""In-kind inventory adoption records and their projection onto a live book (ADR 0124).

An adoption is a live book taking ownership of coins the venue account already holds. It
is never routed to a broker. One adoption is an intent, a FILLED order and one fill, all
with kind/purpose ``ADOPTION``. The order and fill have no venue id, the fill has no fee,
and the fill is already economically applied when it is persisted. A store commits the
records together with this projection in one transaction, so ``replay_unapplied_fills``
never sees an adoption fill, and reconcile treats incomplete adoption evidence as a
fault instead of asking the venue about it.

The projection is the ordinary buy-fill projection: the book's pending stop and target
are staged first, so ``_project_entry`` opens a protected long at the mark exactly as a
venue buy would. A live strategy ledger starts at cash 0 (ADR 0106), so the adoption
debits cash by its notional and the book's equity at the mark is 0.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.trading.fill_ledger import project_fill_economics
from thytrader.trading.ids import uuid7
from thytrader.trading.models import (
    DeploymentMode,
    Fill,
    IntentOrigin,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    resolved_product_id,
    snapshot_positions,
    with_runtime,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.trading.models import DeploymentSnapshot

ADOPTION_CLIENT_ORDER_PREFIX = "adopt:"
ADOPTION_EVIDENCE_INCOMPLETE = "ADOPTION_EVIDENCE_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class AdoptionRecords:
    """The intent, filled order and applied fill that record one adoption."""

    intent: OrderIntent
    order: Order
    fill: Fill


def adoption_records(
    *,
    deployment_id: UUID,
    product_id: str,
    quantity: Decimal,
    mark: Decimal,
    mark_bar_starts_at: datetime,
    now: datetime,
    origin: IntentOrigin,
    idempotency_key: str | None = None,
) -> AdoptionRecords:
    """Build the records of one long adoption of ``quantity`` base at ``mark``.

    ``mark_bar_starts_at`` is the start of the closed candle whose close is the mark; it
    becomes the intent's decision bar. ``now`` stamps the intent, order and fill, and the
    fill's ``economics_applied_at``, because the caller projects it in the same commit.
    No venue observation time is recorded (ADR 0119).

    Raises:
        ValueError: The quantity or mark is not a positive finite decimal, an instant is
            naive, the mark bar starts after ``now``, or the product id is empty.
    """
    _require_positive("quantity", quantity)
    _require_positive("mark", mark)
    if now.tzinfo is None or mark_bar_starts_at.tzinfo is None:
        raise ValueError("Adoption instants must be timezone-aware UTC.")
    if mark_bar_starts_at > now:
        raise ValueError("The adoption mark bar must start before the adoption.")
    if not product_id:
        raise ValueError("An adoption needs a product id.")
    client_order_id = f"{ADOPTION_CLIENT_ORDER_PREFIX}{deployment_id}:{uuid7(now)}"
    intent = OrderIntent(
        id=uuid7(now),
        deployment_id=deployment_id,
        client_order_id=client_order_id,
        purpose=IntentPurpose.ADOPTION,
        side=OrderSide.BUY,
        kind=OrderKind.ADOPTION,
        quantity=quantity,
        created_at=now,
        candle_starts_at=mark_bar_starts_at,
        price=mark,
        status=OrderStatus.FILLED,
        origin=origin,
        idempotency_key=idempotency_key,
        product_id=product_id,
    )
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=intent.id,
        client_order_id=client_order_id,
        side=OrderSide.BUY,
        kind=OrderKind.ADOPTION,
        quantity=quantity,
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=mark,
        filled_quantity=quantity,
        product_id=product_id,
    )
    fill = Fill(
        id=uuid7(now),
        deployment_id=deployment_id,
        order_id=order.id,
        venue_fill_id=f"{client_order_id}:fill",
        price=mark,
        quantity=quantity,
        fee=Decimal(0),
        filled_at=now,
        economics_applied_at=now,
    )
    return AdoptionRecords(intent=intent, order=order, fill=fill)


def project_adoption(
    snapshot: DeploymentSnapshot,
    records: AdoptionRecords,
    *,
    stop_price: Decimal,
    target_price: Decimal | None,
) -> tuple[DeploymentSnapshot, Fill]:
    """Open the adopted long on a flat live product book, with its protection levels.

    The stop and target are staged as the book's pending levels and the mark bar becomes
    the last evaluated bar, so the worker does not evaluate that bar again. The fill is
    then projected like any buy fill: the book goes FLAT to OPEN with ``entered_bar`` at
    the adoption instant's bucket and the existing protection path places the stop.

    Raises:
        ValueError: The book is not live, the records belong to another book or are not
            an adoption, the product book already holds a position, or the stop is not
            below the mark or the target not above it.
    """
    deployment = snapshot.deployment
    order = records.order
    if deployment.mode is not DeploymentMode.LIVE:
        raise ValueError("Inventory adoption is live-only.")
    if (
        order.kind is not OrderKind.ADOPTION
        or records.intent.purpose is not IntentPurpose.ADOPTION
        or {records.intent.deployment_id, order.deployment_id, records.fill.deployment_id}
        != {deployment.id}
    ):
        raise ValueError("The records are not an adoption for this book.")
    product_id = resolved_product_id(order.product_id, deployment)
    if any(
        resolved_product_id(position.product_id, deployment) == product_id
        for position in snapshot_positions(snapshot)
    ):
        raise ValueError("Adoption needs a flat product book.")
    mark = records.fill.price
    _require_positive("stop_price", stop_price)
    if stop_price >= mark or (target_price is not None and target_price <= mark):
        raise ValueError("An adopted long needs a stop below and a target above the mark.")
    instant = records.fill.filled_at
    cleared = with_runtime(deployment, updated_at=instant, clear_pending_levels=True)
    previous = deployment.last_evaluated_bar
    mark_bar = records.intent.candle_starts_at
    staged = with_runtime(
        cleared,
        updated_at=instant,
        pending_stop_price=stop_price,
        pending_target_price=target_price,
        last_evaluated_bar=mark_bar if previous is None or previous < mark_bar else None,
    )
    pending = replace(
        snapshot,
        deployment=staged,
        intents=(*snapshot.intents, records.intent),
        orders=(*snapshot.orders, order),
    )
    return project_fill_economics(
        pending, fill=records.fill, order=order, timeframe=deployment.timeframe
    )


def adoption_evidence_complete(snapshot: DeploymentSnapshot, order: Order) -> bool:
    """True when an adoption order is FILLED and its own applied fills cover it exactly.

    A missing, unapplied, partial or over-covering fill, or a venue id on an order that
    never went to the venue, is incomplete evidence: reconcile pauses the book rather
    than asking the venue about an order it never received.
    """
    fills = tuple(fill for fill in snapshot.fills if fill.order_id == order.id)
    return (
        order.kind is OrderKind.ADOPTION
        and order.status is OrderStatus.FILLED
        and order.venue_order_id is None
        and bool(fills)
        and all(fill.economics_applied_at is not None for fill in fills)
        and sum((fill.quantity for fill in fills), start=Decimal(0))
        == order.quantity
        == order.filled_quantity
    )


def _require_positive(name: str, value: Decimal) -> None:
    """Reject a non-finite or non-positive decimal amount."""
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Adoption {name} must be a positive finite decimal.")
