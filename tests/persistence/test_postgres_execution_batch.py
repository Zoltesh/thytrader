"""The batched snapshot read equals one ``get_deployment`` per book (ADR 0131)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest

from tests.persistence.test_postgres_fill_transaction import _pending_entry_book
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.ids import uuid7
from thytrader.trading.models import (
    DeploymentStatus,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_NOW = datetime(2026, 1, 2, 15, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


async def _book_with_history(store: PostgresExecutionStore, *, orders: int) -> None:
    """One book with intents, orders, fills, two positions and a runtime overlay."""
    created = await store.create_deployment(
        replace(_pending_entry_book(), id=uuid4(), status=DeploymentStatus.STOPPED)
    )
    for index in range(orders):
        at = _NOW + timedelta(minutes=index)
        intent = OrderIntent(
            id=uuid4(),
            deployment_id=created.id,
            client_order_id=f"c-{created.id}-{index}",
            purpose=IntentPurpose.ENTRY,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("1"),
            created_at=at,
            candle_starts_at=at,
            price=Decimal("100") + index,
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
            status=OrderStatus.FILLED if index % 2 == 0 else OrderStatus.OPEN,
            created_at=at,
            updated_at=at,
            price=Decimal("100") + index,
            filled_quantity=Decimal("1") if index % 2 == 0 else Decimal("0"),
            product_id="BTC-USDC",
        )
        await store.save_order(order)
        if index % 2 == 0:
            await store.save_fill(
                Fill(
                    id=uuid7(at),
                    deployment_id=created.id,
                    order_id=order.id,
                    venue_fill_id=f"f-{created.id}-{index}",
                    price=order.price or Decimal("100"),
                    quantity=Decimal("1"),
                    fee=Decimal("0.1"),
                    filled_at=at,
                )
            )
    for product_id in ("BTC-USDC", "ETH-USDC"):
        await store.save_position(
            Position(
                deployment_id=created.id,
                quantity=Decimal("0.5"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                product_id=product_id,
            ),
            deployment_id=created.id,
            product_id=product_id,
        )
    await store.save_instrument_runtime(
        InstrumentRuntime(product_id="ETH-USDC", phase=RuntimePhase.OPEN),
        deployment_id=created.id,
    )


@pytest.mark.anyio
async def test_batched_read_equals_per_book_reads() -> None:
    """Every field, ordering and focused position matches the single loader."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    try:
        for orders in (0, 1, 5):
            await _book_with_history(store, orders=orders)
        bare = await store.create_deployment(replace(_pending_entry_book(), id=uuid4()))
        ids = [item.id for item in await store.list_deployments()]
        assert bare.id in ids
        batched = await store.get_deployments(ids)
        per_book = tuple([await store.get_deployment(item) for item in ids])
        assert batched == per_book
        assert await store.get_deployments(list(reversed(ids))) == tuple(reversed(per_book))
        assert await store.get_deployments([]) == ()
        with pytest.raises(ExecutionStoreError, match=r"Deployment was not found\."):
            await store.get_deployments([*ids, uuid4()])
    finally:
        await dispose(engine)
