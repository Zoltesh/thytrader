"""Real PostgreSQL coverage for the atomic fill ledger (F01) and add-intent counting (F28).

Every test here disposes its engine and opens a fresh one to model a process
restart between steps: nothing survives in Python memory across that boundary,
only what ``PostgresExecutionStore.apply_fill_effect`` actually committed.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import os
from uuid import UUID, uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import delete

from thytrader.execution.fill_ledger import apply_fill
from thytrader.execution.ids import uuid7
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    FillApplication,
    IntentOrigin,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.schema import (
    deployments,
    execution_fills,
    execution_orders,
    execution_positions,
    order_intents,
)

_TEST_DATABASE_URL = os.environ.get("THYTRADER_TEST_DATABASE_URL") or None
pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _database_url() -> str:
    """Return the configured integration URL or fail loudly."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    return _TEST_DATABASE_URL


def _now() -> datetime:
    """Return a fixed 2026-02-01 UTC instant so fixtures are deterministic."""
    return datetime(2026, 2, 1, tzinfo=UTC)


def _discretionary_deployment(*, deployment_id: UUID, cash: Decimal) -> Deployment:
    """Build one discretionary paper deployment pending an entry fill.

    Discretionary deployments have no ``strategy_fingerprint`` foreign key, which
    keeps this fixture independent of the published-strategy tables.
    """
    now = _now()
    return Deployment(
        id=deployment_id,
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="5m",
        cash=cash,
        paper_starting_cash=cash,
        paper_maker_fee_rate=Decimal("0"),
        paper_taker_fee_rate=Decimal("0"),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("50000"),
        pending_target_price=Decimal("200000"),
        created_at=now,
        updated_at=now,
    )


def _entry_intent(*, deployment_id: UUID, quantity: Decimal, client_order_id: str) -> OrderIntent:
    """Build one persisted entry-purpose intent."""
    now = _now()
    return OrderIntent(
        id=uuid7(now),
        deployment_id=deployment_id,
        client_order_id=client_order_id,
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=quantity,
        created_at=now,
        candle_starts_at=now,
        status=OrderStatus.PENDING,
        origin=IntentOrigin.AGENT,
        product_id="BTC-USD",
    )


def _order_for_intent(intent: OrderIntent) -> Order:
    """Build the venue-visible order tied to one persisted intent."""
    now = _now()
    return Order(
        id=uuid7(now),
        deployment_id=intent.deployment_id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=intent.side,
        kind=intent.kind,
        quantity=intent.quantity,
        status=OrderStatus.PENDING,
        created_at=now,
        updated_at=now,
        venue_order_id=f"venue-{intent.client_order_id}",
        product_id="BTC-USD",
    )


async def _cleanup(deployment_id: UUID) -> None:
    """Delete every row this test module could have written for one deployment."""
    engine = create_engine(SecretStr(_database_url()))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(execution_positions).where(
                    execution_positions.c.deployment_id == deployment_id
                )
            )
            await connection.execute(
                delete(execution_fills).where(execution_fills.c.deployment_id == deployment_id)
            )
            await connection.execute(
                delete(execution_orders).where(execution_orders.c.deployment_id == deployment_id)
            )
            await connection.execute(
                delete(order_intents).where(order_intents.c.deployment_id == deployment_id)
            )
            await connection.execute(delete(deployments).where(deployments.c.id == deployment_id))
    finally:
        await dispose(engine)


def test_postgres_fill_application_survives_restart_and_rejects_replay() -> None:
    """F01: one exact fill applies once; a restart plus a replayed fill changes nothing.

    Models "terminate at a write boundary, restart, replay the same fill twice"
    directly against PostgreSQL: each step below opens a brand-new engine so
    nothing survives from Python process memory, only what the prior atomic
    transaction actually committed.
    """
    deployment_id = uuid4()
    expected_cash = Decimal("0")

    async def exercise() -> None:
        nonlocal expected_cash
        engine = create_engine(SecretStr(_database_url()))
        deployment = _discretionary_deployment(deployment_id=deployment_id, cash=Decimal("10000"))
        try:
            store = PostgresExecutionStore(engine)
            await store.create_deployment(deployment)
            intent = _entry_intent(
                deployment_id=deployment_id,
                quantity=Decimal("0.01"),
                client_order_id=f"client-{deployment_id}",
            )
            await store.save_intent(intent)
            order = _order_for_intent(intent)
            await store.save_order(order)

            # Exact repro numbers from the audit's F03 reproduction, reused here for F01:
            # a 0.01-unit fill at 100,000 with a 1-unit fee is a 1,001 total cash debit.
            fill = Fill(
                id=uuid7(_now()),
                deployment_id=deployment_id,
                order_id=order.id,
                venue_fill_id="fill-1",
                price=Decimal("100000"),
                quantity=Decimal("0.01"),
                fee=Decimal("1"),
                filled_at=_now(),
            )
            filled_order = replace(
                order,
                status=OrderStatus.FILLED,
                filled_quantity=fill.quantity,
                updated_at=_now(),
            )
            expected_cash = deployment.cash - (fill.price * fill.quantity) - fill.fee
            position = Position(
                deployment_id=deployment_id,
                quantity=fill.quantity,
                entry_price=fill.price,
                stop_price=deployment.pending_stop_price or Decimal("0"),
                target_price=deployment.pending_target_price or Decimal("0"),
                entered_bar=_now(),
                updated_at=_now(),
                side=PositionSide.LONG,
                product_id="BTC-USD",
                add_count=1,
                last_fill_intent_id=intent.id,
            )
            application = FillApplication(
                fill=fill,
                order=filled_order,
                deployment=replace(
                    deployment,
                    cash=expected_cash,
                    phase=RuntimePhase.OPEN,
                    pending_stop_price=None,
                    pending_target_price=None,
                    updated_at=_now(),
                ),
                position=position,
                clear_position=False,
            )
            first_result = await store.apply_fill_effect(application)
            assert first_result.applied is True
        finally:
            await dispose(engine)

        # Restart: a brand-new engine, as if the process had been killed and relaunched.
        restarted_engine = create_engine(SecretStr(_database_url()))
        try:
            restarted_store = PostgresExecutionStore(restarted_engine)
            after_restart = await restarted_store.get_deployment(deployment_id)
            assert after_restart.deployment.cash == expected_cash
            assert after_restart.position is not None
            assert after_restart.position.quantity == Decimal("0.01")
            assert after_restart.position.add_count == 1
            assert len(after_restart.fills) == 1
            assert after_restart.fills[0].fee == Decimal("1")

            # Replay the exact same fill through the restarted store (a retried
            # reconciliation or a repeated submit-completion call after the restart).
            replay_result = await restarted_store.apply_fill_effect(application)
            assert replay_result.applied is False

            final = await restarted_store.get_deployment(deployment_id)
            assert final.deployment.cash == expected_cash
            assert final.position is not None
            assert final.position.quantity == Decimal("0.01")
            assert len(final.fills) == 1
        finally:
            await dispose(restarted_engine)

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(_cleanup(deployment_id))


def test_postgres_apply_fill_effect_completes_a_previously_recorded_unapplied_fill() -> None:
    """F01: a fill recorded (evidence) but not yet applied still applies exactly once.

    ``save_fill`` alone (the paper immediate-fill evidence path) leaves
    ``applied_at`` null. A later ``apply_fill_effect`` call for that same fill must
    still run the economic effect the first time, and be a no-op on any repeat.
    """
    deployment_id = uuid4()

    async def exercise() -> None:
        engine = create_engine(SecretStr(_database_url()))
        try:
            store = PostgresExecutionStore(engine)
            deployment = _discretionary_deployment(
                deployment_id=deployment_id, cash=Decimal("5000")
            )
            await store.create_deployment(deployment)
            intent = _entry_intent(
                deployment_id=deployment_id,
                quantity=Decimal("0.02"),
                client_order_id=f"client-{deployment_id}",
            )
            await store.save_intent(intent)
            order = _order_for_intent(intent)
            await store.save_order(order)
            fill = Fill(
                id=uuid7(_now()),
                deployment_id=deployment_id,
                order_id=order.id,
                venue_fill_id="fill-recorded-only",
                price=Decimal("1000"),
                quantity=Decimal("0.02"),
                fee=Decimal("0.5"),
                filled_at=_now(),
            )
            # Evidence recorded without an effect yet, exactly the crash window F01 closes.
            await store.save_fill(fill)

            snapshot = await store.get_deployment(deployment_id)
            assert snapshot.position is None
            assert len(snapshot.fills) == 1

            filled_order = replace(order, status=OrderStatus.FILLED, filled_quantity=fill.quantity)
            applied_snapshot = await apply_fill(
                snapshot, fill=fill, order=filled_order, store=store
            )
            assert applied_snapshot.position is not None
            assert applied_snapshot.position.quantity == Decimal("0.02")
            expected_cash = deployment.cash - (fill.price * fill.quantity) - fill.fee
            assert applied_snapshot.deployment.cash == expected_cash

            # A second call for the same fill must not double-apply.
            replayed = await apply_fill(
                applied_snapshot, fill=fill, order=filled_order, store=store
            )
            assert replayed.deployment.cash == expected_cash
            assert replayed.position is not None
            assert replayed.position.quantity == Decimal("0.02")
        finally:
            await dispose(engine)

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(_cleanup(deployment_id))


def test_postgres_ambiguous_venue_id_survives_a_process_crash() -> None:
    """F36: an order persisted with a known venue id but ``UNKNOWN`` status survives a crash.

    Models a successful POST whose follow-up GET failed: the order row is
    persisted with its exact venue id immediately, before any further observation.
    A crash right after that write (a brand-new engine, nothing left in memory)
    must not lose the venue id, and reconciling from it (applying the fill it
    eventually reports) must not require creating a second order.
    """
    deployment_id = uuid4()

    async def exercise() -> None:
        engine = create_engine(SecretStr(_database_url()))
        try:
            store = PostgresExecutionStore(engine)
            deployment = _discretionary_deployment(
                deployment_id=deployment_id, cash=Decimal("10000")
            )
            await store.create_deployment(deployment)
            intent = _entry_intent(
                deployment_id=deployment_id,
                quantity=Decimal("0.01"),
                client_order_id=f"client-{deployment_id}",
            )
            await store.save_intent(intent)
            order = _order_for_intent(intent)
            # The POST accepted this order (venue id known); the follow-up GET failed.
            ambiguous = replace(
                order,
                status=OrderStatus.UNKNOWN,
                venue_order_id="venue-crash-safe",
                reject_reason="order_observation_failed",
            )
            await store.save_order(ambiguous)
        finally:
            # Simulated crash: nothing survives except what was just committed.
            await dispose(engine)

        restarted_engine = create_engine(SecretStr(_database_url()))
        try:
            restarted_store = PostgresExecutionStore(restarted_engine)
            snapshot = await restarted_store.get_deployment(deployment_id)
            recovered = next(
                item for item in snapshot.orders if item.client_order_id == order.client_order_id
            )
            assert recovered.venue_order_id == "venue-crash-safe"
            assert recovered.status is OrderStatus.UNKNOWN

            # Reconciliation resolves the exact same order using only its venue id;
            # no second order or intent is created.
            fill = Fill(
                id=uuid7(_now()),
                deployment_id=deployment_id,
                order_id=recovered.id,
                venue_fill_id="fill-crash-safe-1",
                price=Decimal("100000"),
                quantity=Decimal("0.01"),
                fee=Decimal("1"),
                filled_at=_now(),
            )
            filled_order = replace(
                recovered, status=OrderStatus.FILLED, filled_quantity=fill.quantity
            )
            applied = await apply_fill(
                snapshot, fill=fill, order=filled_order, store=restarted_store
            )
            assert applied.position is not None
            assert applied.position.quantity == Decimal("0.01")
            assert len(applied.orders) == 1
            assert len(applied.intents) == 1
        finally:
            await dispose(restarted_engine)

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(_cleanup(deployment_id))


def test_postgres_add_count_tracks_intents_not_fragments_across_restart() -> None:
    """F28: 20 fragments of one entry intent consume one slot; each add intent, one more.

    Reproduces the audit's acceptance criteria directly against PostgreSQL: a
    non-pyramiding order that fills across 20 fragments must never approach the
    storage-layer ``add_count`` CHECK (1-8), and two further add intents (each
    filled across several fragments) must land on exactly ``add_count == 3``.
    Restarting (a fresh engine) partway through must not change the outcome.
    """
    deployment_id = uuid4()

    async def exercise() -> None:
        engine = create_engine(SecretStr(_database_url()))
        store = PostgresExecutionStore(engine)
        deployment = _discretionary_deployment(deployment_id=deployment_id, cash=Decimal("1000000"))
        await store.create_deployment(deployment)
        entry_intent = _entry_intent(
            deployment_id=deployment_id,
            quantity=Decimal("0.20"),
            client_order_id=f"client-entry-{deployment_id}",
        )
        await store.save_intent(entry_intent)
        entry_order = _order_for_intent(entry_intent)
        await store.save_order(entry_order)

        snapshot = await store.get_deployment(deployment_id)
        for fragment in range(20):
            fill = Fill(
                id=uuid7(_now()),
                deployment_id=deployment_id,
                order_id=entry_order.id,
                venue_fill_id=f"entry-fragment-{fragment}",
                price=Decimal("1000"),
                quantity=Decimal("0.01"),
                fee=Decimal("0"),
                filled_at=_now(),
            )
            snapshot = await apply_fill(snapshot, fill=fill, order=entry_order, store=store)
            if fragment % 5 == 4:
                # Restart boundary: dispose and reopen, then reload durable state only.
                await dispose(engine)
                engine = create_engine(SecretStr(_database_url()))
                store = PostgresExecutionStore(engine)
                snapshot = await store.get_deployment(deployment_id)

        assert snapshot.position is not None
        assert snapshot.position.add_count == 1
        assert snapshot.position.quantity == Decimal("0.20")

        for add_index in range(2):
            add_intent = _entry_intent(
                deployment_id=deployment_id,
                quantity=Decimal("0.05"),
                client_order_id=f"client-add-{add_index}-{deployment_id}",
            )
            await store.save_intent(add_intent)
            add_order = _order_for_intent(add_intent)
            await store.save_order(add_order)
            for fragment in range(5):
                fill = Fill(
                    id=uuid7(_now()),
                    deployment_id=deployment_id,
                    order_id=add_order.id,
                    venue_fill_id=f"add-{add_index}-fragment-{fragment}",
                    price=Decimal("1000"),
                    quantity=Decimal("0.01"),
                    fee=Decimal("0"),
                    filled_at=_now(),
                )
                snapshot = await apply_fill(snapshot, fill=fill, order=add_order, store=store)
            await dispose(engine)
            engine = create_engine(SecretStr(_database_url()))
            store = PostgresExecutionStore(engine)
            snapshot = await store.get_deployment(deployment_id)
            assert snapshot.position is not None
            assert snapshot.position.add_count == 2 + add_index

        assert snapshot.position is not None
        assert snapshot.position.add_count == 3
        assert snapshot.position.quantity == Decimal("0.30")
        await dispose(engine)

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(_cleanup(deployment_id))
