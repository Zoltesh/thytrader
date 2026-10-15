"""PostgreSQL keeps an order's first cancel time and its settlement (ADR 0141)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest

from tests.persistence.test_postgres_fill_transaction import _NOW, _pending_entry_book
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.models import (
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
)

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


@pytest.mark.anyio
async def test_postgres_keeps_the_first_cancel_time_and_the_settlement() -> None:
    """Later writes, an UNKNOWN read failure and a different caller value change neither."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    try:
        created = await store.create_deployment(_pending_entry_book())
        intent = OrderIntent(
            id=uuid4(),
            deployment_id=created.id,
            client_order_id=f"entry-{created.id}",
            purpose=IntentPurpose.ENTRY,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("1"),
            created_at=_NOW,
            candle_starts_at=_NOW,
            price=Decimal("100"),
            product_id="BTC-USDC",
        )
        await store.save_intent(intent)
        order = Order(
            id=uuid4(),
            deployment_id=created.id,
            intent_id=intent.id,
            client_order_id=intent.client_order_id,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("1"),
            status=OrderStatus.OPEN,
            created_at=_NOW,
            updated_at=_NOW,
            price=Decimal("100"),
            product_id="BTC-USDC",
        )
        await store.save_order(order)
        assert (await _stored(store, order)).canceled_at is None

        canceled_at = _NOW + timedelta(minutes=1)
        await store.save_order(replace(order, status=OrderStatus.CANCELED, updated_at=canceled_at))
        assert (await _stored(store, order)).canceled_at == canceled_at

        later = canceled_at + timedelta(minutes=5)
        await store.save_order(replace(order, status=OrderStatus.UNKNOWN, updated_at=later))
        await store.save_order(
            replace(order, status=OrderStatus.CANCELED, updated_at=later, canceled_at=later)
        )
        stored = await _stored(store, order)
        assert stored.status is OrderStatus.CANCELED
        assert stored.canceled_at == canceled_at

        settled_at = later + timedelta(minutes=15)
        await store.save_order(replace(stored, settled_at=settled_at, updated_at=settled_at))
        await store.save_order(replace(stored, updated_at=settled_at + timedelta(minutes=1)))
        final = await _stored(store, order)
        assert final.settled_at == settled_at
        assert final.canceled_at == canceled_at
    finally:
        await dispose(engine)


async def _stored(store: PostgresExecutionStore, order: Order) -> Order:
    """The order as the store reloads it."""
    snapshot = await store.get_deployment(order.deployment_id)
    return next(item for item in snapshot.orders if item.id == order.id)
