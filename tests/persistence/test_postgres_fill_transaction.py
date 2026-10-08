"""PostgreSQL coverage for the atomic fill transaction (ADR 0057 / ADR 0082 regression)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import SecretStr
import pytest

from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.fill_ledger import (
    ingest_fill,
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
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
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from uuid import UUID

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


async def _persist_fill_order(
    store: PostgresExecutionStore,
    deployment_id: UUID,
    *,
    product_id: str,
    purpose: IntentPurpose,
    price: Decimal,
    quantity: Decimal = Decimal("1"),
    status: OrderStatus = OrderStatus.FILLED,
    reported: Decimal = Decimal("1"),
) -> Order:
    """Persist real intent/order rows for a scoped entry or protective execution."""
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment_id,
        client_order_id=str(uuid4()),
        purpose=purpose,
        side=OrderSide.BUY if purpose is IntentPurpose.ENTRY else OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=quantity,
        price=price,
        product_id=product_id,
        created_at=_NOW,
        candle_starts_at=_NOW,
    )
    await store.save_intent(intent)
    return await store.save_order(
        Order(
            id=uuid4(),
            deployment_id=deployment_id,
            intent_id=intent.id,
            client_order_id=intent.client_order_id,
            side=intent.side,
            kind=intent.kind,
            quantity=quantity,
            price=price,
            product_id=product_id,
            status=status,
            filled_quantity=reported,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )


@pytest.mark.anyio
async def test_postgres_atomic_product_runtime_entry_partial_cancel_and_exit_reload() -> None:
    """Product state, economics and parent aggregation survive fresh repository loads."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    try:
        parent = await store.create_deployment(
            replace(_pending_entry_book(), last_evaluated_bar=_NOW + timedelta(hours=1))
        )
        for product_id, stop in (("BTC-USDC", "90"), ("ETH-USDC", "190")):
            await store.save_instrument_runtime(
                InstrumentRuntime(
                    product_id=product_id,
                    phase=RuntimePhase.PENDING_ENTRY,
                    pending_stop_price=Decimal(stop),
                    last_evaluated_bar=_NOW,
                ),
                deployment_id=parent.id,
            )
            order = await _persist_fill_order(
                store,
                parent.id,
                product_id=product_id,
                purpose=IntentPurpose.ENTRY,
                price=Decimal("100" if product_id == "BTC-USDC" else "200"),
            )
            scoped = InstrumentScopedStore(store, product_id)
            await ingest_fill(
                await scoped.get_deployment(parent.id),
                fill=Fill(
                    id=uuid4(),
                    deployment_id=parent.id,
                    order_id=order.id,
                    venue_fill_id=str(uuid4()),
                    price=order.price or Decimal(0),
                    quantity=Decimal("1"),
                    fee=Decimal("0.1"),
                    filled_at=_NOW,
                ),
                order=order,
                store=scoped,
            )
        reloaded_store = PostgresExecutionStore(engine)
        opened = await reloaded_store.get_deployment(parent.id)
        sibling = next(
            runtime for runtime in opened.instrument_runtimes if runtime.product_id == "BTC-USDC"
        )
        assert opened.deployment.phase is RuntimePhase.OPEN
        assert opened.deployment.last_evaluated_bar == parent.last_evaluated_bar
        assert opened.deployment.cash == Decimal("699.8")
        scoped = InstrumentScopedStore(reloaded_store, "ETH-USDC")
        focused = await scoped.get_deployment(parent.id)
        assert focused.deployment.phase is RuntimePhase.OPEN
        assert focused.deployment.pending_stop_price is None
        protection = await _persist_fill_order(
            reloaded_store,
            parent.id,
            product_id="ETH-USDC",
            purpose=IntentPurpose.BRACKET,
            price=Decimal("220"),
            status=OrderStatus.CANCELED,
            reported=Decimal("0.4"),
        )
        partial = Fill(
            id=uuid4(),
            deployment_id=parent.id,
            order_id=protection.id,
            venue_fill_id=str(uuid4()),
            price=Decimal("220"),
            quantity=Decimal("0.2"),
            fee=Decimal("0.01"),
            filled_at=_NOW + timedelta(minutes=1),
        )
        await ingest_fill(
            await scoped.get_deployment(parent.id), fill=partial, order=protection, store=scoped
        )
        waiting = await reloaded_store.get_deployment(parent.id)
        assert unsettled_fill_evidence(waiting)
        assert (
            next(order for order in waiting.orders if order.id == protection.id).status
            is OrderStatus.CANCELED
        )
        assert next(
            order for order in waiting.orders if order.id == protection.id
        ).filled_quantity == Decimal("0.4")
        await ingest_fill(
            await scoped.get_deployment(parent.id),
            fill=replace(partial, id=uuid4(), venue_fill_id=str(uuid4())),
            order=protection,
            store=scoped,
        )
        settled = await reloaded_store.get_deployment(parent.id)
        assert not unsettled_fill_evidence(settled)
        assert not unprojected_inventory_products(settled)
        exit_order = await _persist_fill_order(
            reloaded_store,
            parent.id,
            product_id="ETH-USDC",
            purpose=IntentPurpose.STOP,
            price=Decimal("220"),
            quantity=Decimal("0.6"),
            reported=Decimal("0.6"),
        )
        final_fill = replace(
            partial,
            id=uuid4(),
            venue_fill_id=str(uuid4()),
            order_id=exit_order.id,
            quantity=Decimal("0.6"),
            filled_at=_NOW + timedelta(minutes=2),
        )
        await ingest_fill(
            await scoped.get_deployment(parent.id),
            fill=final_fill,
            order=exit_order,
            store=scoped,
            cooldown_bars=3,
        )
        fresh = PostgresExecutionStore(engine)
        closed = await fresh.get_deployment(parent.id)
        eth = await InstrumentScopedStore(fresh, "ETH-USDC").get_deployment(parent.id)
        assert eth.position is None
        assert eth.deployment.phase is RuntimePhase.FLAT
        assert eth.deployment.cooldown_bars_remaining == 3
        assert closed.deployment.phase is RuntimePhase.OPEN  # BTC remains open.
        assert closed.deployment.last_evaluated_bar == parent.last_evaluated_bar
        assert (
            next(
                runtime
                for runtime in closed.instrument_runtimes
                if runtime.product_id == "BTC-USDC"
            )
            == sibling
        )
        assert closed.deployment.cash == Decimal("919.77")
        replayed = await ingest_fill(
            await InstrumentScopedStore(fresh, "ETH-USDC").get_deployment(parent.id),
            fill=final_fill,
            order=exit_order,
            store=InstrumentScopedStore(fresh, "ETH-USDC"),
            cooldown_bars=3,
        )
        assert not replayed.applied
        assert replayed.snapshot.deployment.cash == closed.deployment.cash
    finally:
        await dispose(engine)
