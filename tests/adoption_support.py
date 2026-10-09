"""Fabricated inventory adoptions for tests (ADR 0124).

Nothing in production creates an adoption yet. These helpers build a live book and commit
one adoption through the public store methods, in the order a single store transaction
will write it: intent, order, the already-applied fill, the position, then the book.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.trading.adoption import AdoptionRecords, adoption_records, project_adoption
from thytrader.trading.fill_ledger import fill_projection_deployment, project_fill_economics
from thytrader.trading.ids import uuid7
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentOrigin,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
    resolved_product_id,
)

if TYPE_CHECKING:
    from thytrader.trading.store import ExecutionStore

ADOPTED_AT = datetime(2026, 10, 9, 14, 20, tzinfo=UTC)
MARK_BAR = datetime(2026, 10, 9, 13, tzinfo=UTC)
FINGERPRINT = "sha256:" + "c" * 64


def live_book(
    *,
    product_id: str = "DOGE-USD",
    timeframe: str = "1h",
    created_at: datetime = ADOPTED_AT - timedelta(minutes=5),
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    kind: DeploymentKind = DeploymentKind.STRATEGY,
    mode: DeploymentMode = DeploymentMode.LIVE,
    strategy_fingerprint: str | None = FINGERPRINT,
    strategy_id: UUID | None = None,
) -> Deployment:
    """One flat live book with zero strategy cash, as live strategies start (ADR 0106)."""
    discretionary = kind is DeploymentKind.DISCRETIONARY
    return Deployment(
        id=uuid7(created_at),
        strategy_fingerprint=None if discretionary else strategy_fingerprint,
        strategy_id=None if discretionary else (strategy_id or UUID(int=124)),
        product_id=product_id,
        mode=mode,
        kind=kind,
        status=status,
        cash=Decimal(0),
        initial_equity=Decimal(0),
        phase=RuntimePhase.FLAT,
        created_at=created_at,
        updated_at=created_at,
        timeframe=timeframe,
    )


def records_for(
    book: Deployment,
    *,
    quantity: Decimal = Decimal(100),
    mark: Decimal = Decimal("0.2"),
    now: datetime = ADOPTED_AT,
    mark_bar: datetime = MARK_BAR,
) -> AdoptionRecords:
    """The records of one long adoption into ``book``."""
    return adoption_records(
        deployment_id=book.id,
        product_id=book.product_id,
        quantity=quantity,
        mark=mark,
        mark_bar_starts_at=mark_bar,
        now=now,
        origin=IntentOrigin.HUMAN,
    )


async def persist_adoption(
    store: ExecutionStore,
    snapshot: DeploymentSnapshot,
    records: AdoptionRecords,
    *,
    stop_price: Decimal = Decimal("0.15"),
    target_price: Decimal | None = Decimal("0.3"),
) -> DeploymentSnapshot:
    """Project one adoption and write every record, returning the reloaded book."""
    projected, fill = project_adoption(
        snapshot, records, stop_price=stop_price, target_price=target_price
    )
    await store.save_intent(records.intent)
    await store.save_order(records.order)
    await store.save_fill(fill)
    product_id = resolved_product_id(records.order.product_id, snapshot.deployment)
    await store.save_position(
        projected.position, deployment_id=snapshot.deployment.id, product_id=product_id
    )
    await store.save_deployment(fill_projection_deployment(snapshot, projected))
    return await store.get_deployment(snapshot.deployment.id)


async def adopted_book(
    store: ExecutionStore,
    *,
    book: Deployment | None = None,
    quantity: Decimal = Decimal(100),
    mark: Decimal = Decimal("0.2"),
    stop_price: Decimal = Decimal("0.15"),
    target_price: Decimal | None = Decimal("0.3"),
) -> DeploymentSnapshot:
    """Create a live book and adopt ``quantity`` base into it at ``mark``."""
    created = await store.create_deployment(book or live_book())
    snapshot = await store.get_deployment(created.id)
    records = records_for(created, quantity=quantity, mark=mark)
    return await persist_adoption(
        store, snapshot, records, stop_price=stop_price, target_price=target_price
    )


def executed_fill(
    snapshot: DeploymentSnapshot,
    *,
    purpose: IntentPurpose,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    minutes: int,
    decision_bar: datetime = MARK_BAR,
    kind: OrderKind = OrderKind.MARKETABLE,
) -> DeploymentSnapshot:
    """Apply one venue-executed fill of ``purpose``, ``minutes`` after the adoption instant."""
    at = ADOPTED_AT + timedelta(minutes=minutes)
    deployment = snapshot.deployment
    intent = OrderIntent(
        id=uuid7(at),
        deployment_id=deployment.id,
        client_order_id=f"{purpose.value}-{minutes}",
        purpose=purpose,
        side=side,
        kind=kind,
        quantity=quantity,
        created_at=at,
        candle_starts_at=decision_bar,
        product_id=deployment.product_id,
    )
    order = Order(
        id=uuid7(at),
        deployment_id=deployment.id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=side,
        kind=kind,
        quantity=quantity,
        status=OrderStatus.FILLED,
        created_at=at,
        updated_at=at,
        filled_quantity=quantity,
        venue_order_id=f"venue-{minutes}",
        product_id=deployment.product_id,
    )
    fill = Fill(
        id=uuid7(at),
        deployment_id=deployment.id,
        order_id=order.id,
        venue_fill_id=f"venue-fill-{minutes}",
        price=price,
        quantity=quantity,
        fee=Decimal("0.01"),
        filled_at=at,
    )
    staged = replace(
        snapshot,
        deployment=replace(deployment, pending_stop_price=Decimal("0.15")),
        intents=(*snapshot.intents, intent),
        orders=(*snapshot.orders, order),
    )
    projected, _stamped = project_fill_economics(staged, fill=fill, order=order)
    return projected
