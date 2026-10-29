"""A pyramid add counts toward the add limit on PostgreSQL exactly as in memory.

PostgreSQL inserts the fill evidence before it projects economics, so the projection must
not count the fill it is applying as an earlier fill of the same order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.persistence.test_postgres_futures_books import _run
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.fill_ledger import ingest_fill
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.trading.store import ExecutionStore

_NOW = datetime(2026, 3, 2, tzinfo=UTC)


def _intent(deployment: Deployment, *, name: str, quantity: str) -> OrderIntent:
    return OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id=name,
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal(quantity),
        price=Decimal("100"),
        created_at=_NOW,
        candle_starts_at=_NOW,
        product_id="BTC-USD",
    )


def _order(intent: OrderIntent, *, pyramid_add: bool) -> Order:
    return Order(
        id=uuid4(),
        deployment_id=intent.deployment_id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=intent.quantity,
        price=Decimal("100"),
        status=OrderStatus.OPEN,
        created_at=_NOW,
        updated_at=_NOW,
        venue_order_id=intent.client_order_id,
        product_id="BTC-USD",
        pyramid_add=pyramid_add,
    )


def _fill(deployment: Deployment, order: Order, *, name: str, quantity: str, hour: int) -> Fill:
    return Fill(
        id=uuid7(_NOW + timedelta(hours=hour)),
        deployment_id=deployment.id,
        order_id=order.id,
        venue_fill_id=f"{name}-{deployment.id.hex[:8]}",
        price=Decimal("100"),
        quantity=Decimal(quantity),
        fee=Decimal("0"),
        filled_at=_NOW + timedelta(hours=hour),
    )


async def _scale_in(store: ExecutionStore) -> list[int]:
    """Enter, then add once in two fragments; return add_count after each fill."""
    deployment = await store.create_deployment(
        Deployment(
            id=uuid4(),
            strategy_fingerprint=None,
            strategy_id=None,
            kind=DeploymentKind.DISCRETIONARY,
            product_id="BTC-USD",
            timeframe="1h",
            mode=DeploymentMode.PAPER,
            status=DeploymentStatus.RUNNING,
            paper_starting_cash=Decimal(10000),
            paper_maker_fee_rate=Decimal(0),
            paper_taker_fee_rate=Decimal(0),
            cash=Decimal(10000),
            initial_equity=Decimal(10000),
            phase=RuntimePhase.PENDING_ENTRY,
            pending_stop_price=Decimal("90"),
            pending_target_price=Decimal("120"),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    tag = deployment.id.hex[:8]
    intents = (
        _intent(deployment, name=f"entry-{tag}", quantity="1"),
        _intent(deployment, name=f"add-{tag}", quantity="1"),
    )
    for intent in intents:
        await store.save_intent(intent)
    entry = _order(intents[0], pyramid_add=False)
    add = _order(intents[1], pyramid_add=True)
    counts: list[int] = []
    for order, fill in (
        (entry, _fill(deployment, entry, name="entry-1", quantity="1", hour=1)),
        (add, _fill(deployment, add, name="add-1", quantity="0.5", hour=2)),
        (add, _fill(deployment, add, name="add-2", quantity="0.5", hour=3)),
    ):
        await store.save_order(order)
        snapshot = await store.get_deployment(deployment.id)
        result = await ingest_fill(snapshot, fill=fill, order=order, store=store)
        assert result.applied
        assert result.snapshot.position is not None
        counts.append(result.snapshot.position.add_count)
    return counts


@pytest.mark.anyio
async def test_in_memory_add_counts_once_per_add_order() -> None:
    """Reference behaviour: the add's first fill counts it; its second fragment does not."""
    entered, *added = await _scale_in(InMemoryExecutionStore())
    assert added == [entered + 1, entered + 1]


@pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None, reason="Disposable PostgreSQL required."
)
def test_postgres_add_counts_once_per_add_order() -> None:
    """L4: PostgreSQL counts the add, so the pyramid cap is reachable."""

    async def body(engine: AsyncEngine) -> None:
        entered, *added = await _scale_in(PostgresExecutionStore(engine))
        assert added == [entered + 1, entered + 1]

    _run(body)
