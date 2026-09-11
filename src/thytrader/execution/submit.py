"""Persist an order intent before submitting it to a broker."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import (
    Fill,
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
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle


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
) -> Order:
    """Record intent, submit, then persist the venue snapshot and any immediate fill."""
    now = utc_now()
    client_order_id = (
        f"{deployment_id}:{purpose.value}:{candle.starts_at.strftime('%Y%m%dT%H')}:{uuid7(now)}"
    )[:128]
    intent = OrderIntent(
        id=uuid7(now),
        deployment_id=deployment_id,
        client_order_id=client_order_id,
        purpose=purpose,
        side=side,
        kind=kind,
        quantity=quantity,
        price=price,
        created_at=now,
        candle_starts_at=candle.starts_at,
        status=OrderStatus.PENDING,
    )
    await store.save_intent(intent)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=intent.id,
        client_order_id=client_order_id,
        side=side,
        kind=kind,
        quantity=quantity,
        price=price,
        status=OrderStatus.PENDING,
        created_at=now,
        updated_at=now,
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
        )
    except (BrokerError, ValueError, TimeoutError) as error:
        unknown = replace(
            order,
            status=OrderStatus.UNKNOWN,
            reject_reason=type(error).__name__,
            updated_at=utc_now(),
        )
        await store.save_order(unknown)
        return unknown
    submitted = replace(
        order,
        venue_order_id=result.venue_order_id,
        status=result.status,
        filled_quantity=result.filled_quantity,
        reject_reason=result.reject_reason,
        updated_at=utc_now(),
    )
    await store.save_order(submitted)
    if result.status is OrderStatus.FILLED and result.fill_price is not None:
        fill = Fill(
            id=uuid7(utc_now()),
            deployment_id=deployment_id,
            order_id=submitted.id,
            venue_fill_id=f"{result.venue_order_id}:immediate",
            price=result.fill_price,
            quantity=result.filled_quantity or quantity,
            fee=result.fill_fee,
            filled_at=utc_now(),
        )
        await store.save_fill(fill)
    return submitted
