"""PostgreSQL coverage for the atomic fill transaction (ADR 0057 / ADR 0082 regression)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest

from thytrader.execution.fill_ledger import ingest_fill
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
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
    Position,
    RuntimePhase,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_NOW = datetime(2026, 1, 2, 15, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _pending_entry_book() -> Deployment:
    """Return a paper discretionary book waiting on one resting entry."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USDC",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="1h",
        cash=Decimal("1000"),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("1000"),
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
        initial_equity=Decimal("1000"),
        baseline_equity=Decimal("1000"),
        utc_day_open_equity=Decimal("1000"),
        utc_day_open_at=_NOW,
        high_water_mark_equity=Decimal("1000"),
    )


@pytest.mark.anyio
async def test_postgres_fill_transaction_applies_an_entry_fill() -> None:
    """A filled entry ingests through Postgres and opens the position with a new revision.

    Regression: the deployment UPDATE passed ``revision`` twice (once inside the mutable
    values map), so every Postgres fill ingest raised TypeError and live fills were never
    applied.
    """
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
            status=OrderStatus.FILLED,
            created_at=_NOW,
            updated_at=_NOW,
            price=Decimal("100"),
            filled_quantity=Decimal("1"),
            product_id="BTC-USDC",
        )
        await store.save_order(order)
        snapshot = await store.get_deployment(created.id)
        fill = Fill(
            id=uuid7(utc_now()),
            deployment_id=created.id,
            order_id=order.id,
            venue_fill_id=f"venue-fill-{created.id}",
            price=Decimal("100"),
            quantity=Decimal("1"),
            fee=Decimal("0.1"),
            filled_at=_NOW,
        )

        result = await ingest_fill(snapshot, fill=fill, order=order, store=store)

        loaded = await store.get_deployment(created.id)
        assert result.snapshot.position is not None
        assert loaded.position is not None
        assert loaded.position.quantity == Decimal("1")
        assert await store.get_position_entry_fees(
            loaded.position, product_id="BTC-USDC"
        ) == Decimal("0.1")
        assert await store.get_position_entry_fees(loaded.position, product_id="ETH-USDC") is None
        assert (
            await store.get_position_entry_fees(
                replace(loaded.position, quantity=Decimal("0.5")), product_id="BTC-USDC"
            )
            is None
        )
        assert loaded.deployment.revision == snapshot.deployment.revision + 1
        assert loaded.deployment.cash == Decimal("899.9")
        replayed = await ingest_fill(
            loaded, fill=replace(fill, id=uuid7(utc_now())), order=order, store=store
        )
        assert replayed.snapshot.position is not None
        after_replay = (await store.get_deployment(created.id)).position
        assert after_replay is not None
        assert after_replay.quantity == Decimal("1")
    finally:
        await dispose(engine)


@pytest.mark.anyio
async def test_fee_reads_isolate_current_applied_product_fills() -> None:
    """SQL and memory agree on legacy product ids, partial exits, and irrelevant evidence."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    try:
        for store in (PostgresExecutionStore(engine), InMemoryExecutionStore()):
            deployment = await store.create_deployment(
                replace(_pending_entry_book(), status=DeploymentStatus.STOPPED)
            )
            position = Position(
                deployment_id=deployment.id,
                quantity=Decimal("1.5"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                product_id="BTC-USDC",
            )
            # Deliberately insert out of time order. Only the applied BTC entry/partial exit count.
            rows = (
                (2, OrderSide.SELL, "0.5", "7", "BTC-USDC", True),
                (1, OrderSide.BUY, "2", "0.4", "", True),
                (-1, OrderSide.BUY, "2", "9", "BTC-USDC", True),
                (3, OrderSide.BUY, "1", "8", "BTC-USDC", False),
                (4, OrderSide.BUY, "1", "6", "ETH-USDC", True),
            )
            for minute, side, quantity, fee, product, applied in rows:
                at = _NOW + timedelta(minutes=minute)
                intent = OrderIntent(
                    id=uuid4(),
                    deployment_id=deployment.id,
                    client_order_id=str(uuid4()),
                    purpose=IntentPurpose.ENTRY,
                    side=side,
                    kind=OrderKind.POST_ONLY_LIMIT,
                    quantity=Decimal(quantity),
                    price=Decimal("100"),
                    created_at=at,
                    candle_starts_at=at,
                    product_id=product,
                )
                await store.save_intent(intent)
                order = Order(
                    id=uuid4(),
                    deployment_id=deployment.id,
                    intent_id=intent.id,
                    client_order_id=intent.client_order_id,
                    side=side,
                    kind=OrderKind.POST_ONLY_LIMIT,
                    quantity=Decimal(quantity),
                    price=Decimal("100"),
                    status=OrderStatus.FILLED,
                    created_at=at,
                    updated_at=at,
                    product_id=product,
                )
                await store.save_order(order)
                await store.save_fill(
                    Fill(
                        id=uuid4(),
                        deployment_id=deployment.id,
                        order_id=order.id,
                        venue_fill_id=str(uuid4()),
                        price=Decimal("100"),
                        quantity=Decimal(quantity),
                        fee=Decimal(fee),
                        filled_at=at,
                        economics_applied_at=at if applied else None,
                    )
                )
            assert await store.get_position_entry_fees(position, product_id="BTC-USDC") == Decimal(
                "0.3"
            )
            assert await store.get_position_entry_fees(
                replace(position, quantity=Decimal("1")), product_id="ETH-USDC"
            ) == Decimal("6")
            assert await store.get_position_entry_fees(position, product_id="SOL-USDC") is None
    finally:
        await dispose(engine)
