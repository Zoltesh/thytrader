"""Persist an order intent before submitting it to a broker."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.broker import BrokerError
from thytrader.execution.paper import PaperBroker
from thytrader.memory.recording import maybe_record_submitted_intent
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.models import (
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    IntentOrigin,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle
    from thytrader.trading.store import ExecutionStore


async def submit_intent(
    *,
    store: ExecutionStore,
    broker: Broker,
    deployment_id: UUID,
    product_id: str,
    purpose: IntentPurpose,
    side: OrderSide,
    kind: OrderKind,
    quantity: Decimal,
    price: Decimal | None,
    candle: Candle,
    stop_trigger_price: Decimal | None = None,
    take_profit_price: Decimal | None = None,
    origin: IntentOrigin = IntentOrigin.RUNTIME,
    idempotency_key: str | None = None,
    pyramid_add: bool = False,
) -> Order:
    """Record intent, submit, then persist the venue snapshot and any immediate fill.

    Inventory adoption never reaches a broker (ADR 0124). It is refused here before any
    intent is written, so a misrouted adoption leaves no PENDING or UNKNOWN order behind.
    """
    if kind is OrderKind.ADOPTION or purpose is IntentPurpose.ADOPTION:
        raise ValueError("Inventory adoption is recorded in-kind and is never submitted.")
    now = utc_now()
    stamp = candle.starts_at.strftime("%Y%m%dT%H%M")
    client_order_id = f"{deployment_id}:{purpose.value}:{stamp}:{uuid7(now)}"[:128]
    intent = OrderIntent(
        id=uuid7(now),
        deployment_id=deployment_id,
        client_order_id=client_order_id,
        purpose=purpose,
        side=side,
        kind=kind,
        quantity=quantity,
        price=price,
        stop_trigger_price=stop_trigger_price,
        take_profit_price=take_profit_price,
        created_at=now,
        candle_starts_at=candle.starts_at,
        status=OrderStatus.PENDING,
        origin=origin,
        idempotency_key=idempotency_key,
        product_id=product_id,
    )
    try:
        await store.save_intent(intent)
    except ExecutionConflictError:
        existing = await _existing_order_for_key(store, idempotency_key)
        if existing is not None:
            return existing
        raise
    await _record_why(store, intent=intent, deployment_id=deployment_id)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=intent.id,
        client_order_id=client_order_id,
        side=side,
        kind=kind,
        quantity=quantity,
        price=price,
        stop_trigger_price=stop_trigger_price,
        take_profit_price=take_profit_price,
        status=OrderStatus.PENDING,
        created_at=now,
        updated_at=now,
        product_id=product_id,
        pyramid_add=pyramid_add,
    )
    await store.save_order(order)
    try:
        result = await broker.place_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            kind=kind,
            quantity=quantity,
            price=price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
        )
    except (BrokerError, ValueError, TimeoutError) as error:
        unknown = replace(
            order,
            status=OrderStatus.UNKNOWN,
            reject_reason=type(error).__name__,
            updated_at=utc_now(),
        )
        await store.save_order(unknown)
        await record_execution_audit(
            action="order_submit_unconfirmed",
            outcome=AuditEventOutcome.FAILURE,
            detail=(
                f"deployment_id={deployment_id} client_order_id={client_order_id} "
                f"purpose={purpose.value} error={type(error).__name__}: create outcome is "
                "ambiguous; the order stays UNKNOWN and is looked up by client_order_id. "
                "It is never re-submitted automatically."
            ),
            product_id=product_id,
        )
        return unknown
    submitted = replace(
        order,
        venue_order_id=result.venue_order_id,
        status=result.status,
        filled_quantity=result.filled_quantity,
        reject_reason=result.reject_reason,
        attached_child_venue_order_id=result.attached_child_venue_order_id,
        updated_at=utc_now(),
    )
    await store.save_order(submitted)
    if result.status is OrderStatus.REJECTED:
        await record_execution_audit(
            action="order_submit_rejected",
            outcome=AuditEventOutcome.INFO,
            detail=(
                f"deployment_id={deployment_id} client_order_id={client_order_id} "
                f"purpose={purpose.value} reason={result.reject_reason or 'rejected'}: "
                "venue definitively rejected the create; no order exists."
            ),
            product_id=product_id,
        )
    if result.attached_child_venue_order_id:
        child = Order(
            id=uuid7(utc_now()),
            deployment_id=deployment_id,
            intent_id=intent.id,
            client_order_id=f"{client_order_id}:child"[:128],
            side=OrderSide.SELL if side is OrderSide.BUY else OrderSide.BUY,
            kind=OrderKind.TRIGGER_BRACKET,
            quantity=quantity,
            price=take_profit_price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
            status=OrderStatus.OPEN,
            created_at=now,
            updated_at=utc_now(),
            venue_order_id=result.attached_child_venue_order_id,
            product_id=product_id,
            parent_order_id=submitted.id,
        )
        await store.save_order(child)
    if (
        isinstance(broker, PaperBroker)
        and result.status is OrderStatus.FILLED
        and result.fill_price is not None
    ):
        fill = Fill(
            id=uuid7(utc_now()),
            deployment_id=deployment_id,
            order_id=submitted.id,
            venue_fill_id=f"{result.venue_order_id}:immediate",
            price=result.fill_price,
            quantity=result.filled_quantity or quantity,
            fee=result.fill_fee,
            filled_at=candle.starts_at,
        )
        await store.save_fill(fill)
    return submitted


async def _existing_order_for_key(
    store: ExecutionStore, idempotency_key: str | None
) -> Order | None:
    """Return the venue order already recorded for a stable intent identity."""
    if not idempotency_key:
        return None
    existing = await store.get_intent_by_idempotency_key(idempotency_key)
    if existing is None:
        return None
    snapshot = await store.get_deployment(existing.deployment_id)
    for order in snapshot.orders:
        if order.intent_id == existing.id:
            return order
    return None


async def _record_why(
    store: ExecutionStore,
    *,
    intent: OrderIntent,
    deployment_id: UUID,
) -> None:
    """Write a why-trade row when a trade-reason scope is bound."""
    try:
        snapshot = await store.get_deployment(deployment_id)
    except ExecutionStoreError:
        return
    await maybe_record_submitted_intent(intent=intent, snapshot=snapshot)
