"""Offline PostgreSQL interleavings using scratch schemas and independent engines."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.execution.test_adr_0110_stopped_lifecycle import (
    _as_market_data,
    _candle,
    _PricedPreview,
    _product,
)
from tests.execution.test_lifecycle_safety import _multi_strategy, _QuantityVenue
from tests.loop_patching import patch_loop_global
from tests.persistence.test_postgres_fill_transaction import (
    _NOW,
    _pending_entry_book,
    _persist_fill_order,
)
from tests.risk.test_loss_scope import _TODAY, _policy
from tests.risk.test_safety_evidence import sibling_loss
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.discretionary_request import parse_discretionary_request
from thytrader.execution.leases import RevisionFencedStore
from thytrader.execution.loop import (
    _apply_circuit_breakers,
    _persist_performance,
    maintain_open_inventory,
)
from thytrader.persistence import postgres_execution
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.fill_ledger import ingest_fill
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    IntentPurpose,
    LifecycleCommand,
    OrderIntent,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.trading.models import Deployment, DeploymentSnapshot, Order

_TEST_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(_TEST_URL is None, reason="Offline PostgreSQL URL required."),
]


@pytest.fixture
async def engines() -> AsyncIterator[tuple[AsyncEngine, AsyncEngine]]:
    """Create isolated current-schema tables, never downgrade or alter the shared base."""
    assert _TEST_URL is not None
    schema = f"core_boundary_{uuid4().hex}"
    admin = create_async_engine(_TEST_URL)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    first = create_async_engine(
        _TEST_URL,
        connect_args={
            "server_settings": {"search_path": schema, "application_name": f"{schema}_first"}
        },
    )
    second = create_async_engine(
        _TEST_URL,
        connect_args={
            "server_settings": {"search_path": schema, "application_name": f"{schema}_second"}
        },
    )
    try:
        async with first.begin() as connection:
            # LIKE retains head-schema columns, checks and unique indexes without unrelated
            # portfolio JSON model DDL. No shared tables or migrations are mutated.
            for table in (
                "deployments",
                "order_intents",
                "execution_orders",
                "execution_fills",
                "execution_positions",
                "execution_instrument_state",
                "fleet_entry_inhibition",
            ):
                await connection.execute(
                    text(f'CREATE TABLE "{table}" (LIKE public."{table}" INCLUDING ALL)')
                )
            await connection.execute(
                text(
                    "ALTER TABLE execution_fills ADD CONSTRAINT ux_execution_fills_venue "
                    "UNIQUE (deployment_id, venue_fill_id)"
                )
            )
            # LIKE does not copy foreign keys; restore the execution relationships so
            # fixture orders/fills must satisfy the actual persistence boundary.
            for statement in (
                "ALTER TABLE order_intents ADD FOREIGN KEY (deployment_id) "
                "REFERENCES deployments(id)",
                "ALTER TABLE execution_orders ADD FOREIGN KEY (deployment_id) "
                "REFERENCES deployments(id)",
                "ALTER TABLE execution_orders ADD FOREIGN KEY (intent_id) "
                "REFERENCES order_intents(id)",
                "ALTER TABLE execution_fills ADD FOREIGN KEY (deployment_id) "
                "REFERENCES deployments(id)",
                "ALTER TABLE execution_fills ADD FOREIGN KEY (order_id) "
                "REFERENCES execution_orders(id)",
                "ALTER TABLE execution_positions ADD FOREIGN KEY (deployment_id) "
                "REFERENCES deployments(id)",
                "ALTER TABLE execution_instrument_state ADD FOREIGN KEY (deployment_id) "
                "REFERENCES deployments(id)",
            ):
                await connection.execute(text(statement))
            await connection.execute(
                text(
                    "INSERT INTO fleet_entry_inhibition (mode, inhibited, revision, updated_at) "
                    "VALUES ('live', false, 0, now()), ('paper', false, 0, now())"
                )
            )
        yield first, second
    finally:
        await first.dispose()
        await second.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def _fill(order: Order, *, quantity: str = "1") -> Fill:
    """Return distinct exact economics for a real persisted order."""
    return Fill(
        id=uuid4(),
        deployment_id=order.deployment_id,
        order_id=order.id,
        venue_fill_id=str(uuid4()),
        price=order.price or Decimal(0),
        quantity=Decimal(quantity),
        fee=Decimal("0.1"),
        filled_at=_NOW,
    )


async def _seed(
    store: PostgresExecutionStore, *, secondary: bool
) -> tuple[Deployment, tuple[Order, Order]]:
    """Persist both valid entry intents and focused runtime geometry before racing fills."""
    parent = await store.create_deployment(
        replace(
            _pending_entry_book(),
            product_id="BTC-USD",
            mode=DeploymentMode.LIVE,
            paper_starting_cash=None,
            paper_maker_fee_rate=None,
            paper_taker_fee_rate=None,
        )
    )
    orders = []
    for product_id in ("BTC-USD", "ETH-USD" if secondary else "BTC-USD"):
        await store.save_instrument_runtime(
            InstrumentRuntime(
                product_id,
                RuntimePhase.PENDING_ENTRY,
                pending_stop_price=Decimal("90"),
                pending_target_price=Decimal("120"),
            ),
            deployment_id=parent.id,
        )
        orders.append(
            await _persist_fill_order(
                store,
                parent.id,
                product_id=product_id,
                purpose=IntentPurpose.ENTRY,
                price=Decimal("100"),
            )
        )
    return parent, (orders[0], orders[1])


@pytest.mark.parametrize("secondary", [False, True])
async def test_distinct_concurrent_fills_serialize_projection_and_duplicate_replay(
    engines: tuple[AsyncEngine, AsyncEngine], monkeypatch: pytest.MonkeyPatch, secondary: bool
) -> None:
    """Hold first post-parent-read snapshot; second must block on the existing parent lock."""
    first, second = engines
    store_a, store_b = PostgresExecutionStore(first), PostgresExecutionStore(second)
    parent, orders = await _seed(store_a, secondary=secondary)
    original_snapshot = postgres_execution._snapshot
    first_read, second_read, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    visited: set[AsyncEngine] = set()

    async def held_snapshot(
        connection: postgres_execution.AsyncConnection, deployment: Deployment
    ) -> DeploymentSnapshot:
        """Deterministically expose a shared old projection, or the second row-lock wait."""
        engine = connection.engine
        if engine not in visited:
            visited.add(engine)
            (first_read if engine is first else second_read).set()
            await release.wait()
        return await original_snapshot(connection, deployment)

    fill_a, fill_b = _fill(orders[0]), _fill(orders[1])
    scope_a = InstrumentScopedStore(
        RevisionFencedStore(store_a, parent.id, parent.revision), "BTC-USD"
    )
    scope_b = InstrumentScopedStore(
        RevisionFencedStore(store_b, parent.id, parent.revision), orders[1].product_id
    )
    before_a, before_b = (
        await scope_a.get_deployment(parent.id),
        await scope_b.get_deployment(parent.id),
    )
    # The input snapshots are irrelevant to store projection; hold only transaction reads.
    monkeypatch.setattr(postgres_execution, "_snapshot", held_snapshot)
    visited.clear()
    first_read.clear()
    second_read.clear()
    task_a = asyncio.create_task(ingest_fill(before_a, fill=fill_a, order=orders[0], store=scope_a))
    task_b: asyncio.Task[object] | None = None
    blocked = False
    try:
        await asyncio.wait_for(first_read.wait(), timeout=5)
        task_b = asyncio.create_task(
            ingest_fill(before_b, fill=fill_b, order=orders[1], store=scope_b)
        )
        # Inspect actual PG blocking rather than assuming a delay is serialization.
        async with first.connect() as observer:
            for _ in range(500):
                blocked = bool(
                    await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name LIKE 'core_boundary_%_second' "
                            "AND cardinality(pg_blocking_pids(pid)) > 0)"
                        )
                    )
                )
                if blocked or second_read.is_set():
                    break
                await asyncio.sleep(0.01)
        assert blocked or second_read.is_set(), (
            "Second transaction never reached the read boundary."
        )
        release.set()
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=10)
    finally:
        release.set()
        for task in (task_a, task_b):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (task_a, task_b) if t is not None), return_exceptions=True
        )
    monkeypatch.setattr(postgres_execution, "_snapshot", original_snapshot)
    fresh = PostgresExecutionStore(second)
    loaded = await fresh.get_accounting_snapshot(parent.id)
    assert loaded.deployment.cash == Decimal("799.8")
    assert sum(p.quantity for p in loaded.positions) == Decimal("2")
    assert len(loaded.fills) == 2 and all(f.economics_applied_at is not None for f in loaded.fills)
    assert loaded.deployment.revision == parent.revision + 2
    assert all(r.phase is RuntimePhase.OPEN for r in loaded.instrument_runtimes)
    for order, fill in zip(orders, (fill_a, fill_b), strict=True):
        replay = await ingest_fill(loaded, fill=replace(fill, id=uuid4()), order=order, store=fresh)
        assert not replay.applied
    final = await fresh.get_deployment(parent.id)
    assert final.deployment.cash == loaded.deployment.cash
    assert final.deployment.revision == loaded.deployment.revision
    assert blocked, "Projection was correct without proving the required parent serialization."
    broker = _QuantityVenue()
    for position in final.positions:
        reloaded = await fresh.get_accounting_snapshot(parent.id)
        scope = InstrumentScopedStore(
            RevisionFencedStore(fresh, parent.id, reloaded.deployment.revision), position.product_id
        )
        await maintain_open_inventory(
            await scope.get_deployment(parent.id),
            strategy=_multi_strategy(),
            product=_product(position.product_id),
            candles=(_candle(),),
            broker=broker,
            store=scope,
        )
    assert {item.product_id: item.quantity for item in broker.submissions} == {
        position.product_id: position.quantity for position in final.positions
    }


class _WriteRaceStore(PostgresExecutionStore):
    """Race an independent engine's fill between wrapper reads and conditional save."""

    other: PostgresExecutionStore
    race_order: Order | None = None

    async def _race(self, deployment_id: UUID) -> None:
        """Apply the independently committed fill at either old or new wrapper write boundary."""
        order = self.race_order
        if order is not None:
            self.race_order = None
            await ingest_fill(
                await self.other.get_deployment(deployment_id),
                fill=_fill(order),
                order=order,
                store=self.other,
            )

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Catch the old separate runtime commit as well as the atomic replacement."""
        await self._race(deployment_id)
        await super().save_instrument_runtime(runtime, deployment_id=deployment_id)

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Apply the fill before delegating the old CAS, including any optional runtime."""
        await self._race(deployment.id)
        if instrument_runtime is None:
            return await super().save_deployment(deployment, expected_revision=expected_revision)
        return await super().save_deployment(
            deployment, expected_revision=expected_revision, instrument_runtime=instrument_runtime
        )


@pytest.mark.parametrize("scope_outer", [True, False])
async def test_real_pg_scoped_cas_rejects_all_effects_then_reload_protects(
    engines: tuple[AsyncEngine, AsyncEngine], scope_outer: bool
) -> None:
    """Rejected performance save cannot restore pending geometry after concurrent ETH fill."""
    first, second = engines
    store = _WriteRaceStore(first)
    store.other = PostgresExecutionStore(second)
    parent, orders = await _seed(store, secondary=True)
    scoped = (
        InstrumentScopedStore(RevisionFencedStore(store, parent.id, parent.revision), "ETH-USD")
        if scope_outer
        else RevisionFencedStore(
            InstrumentScopedStore(store, "ETH-USD"), parent.id, parent.revision
        )
    )
    before = await scoped.get_deployment(parent.id)
    store.race_order = orders[1]
    after = await _persist_performance(
        before, store=scoped, mark_price=Decimal("101"), product_id="ETH-USD"
    )
    assert after.deployment.phase is RuntimePhase.OPEN
    assert after.deployment.pending_stop_price is None
    assert after.deployment.cash == Decimal("899.9")
    full = await store.other.get_deployment(parent.id)
    assert full.deployment.revision == parent.revision + 1
    assert (
        next(r for r in full.instrument_runtimes if r.product_id == "BTC-USD").phase
        is RuntimePhase.PENDING_ENTRY
    )
    fresh = InstrumentScopedStore(
        RevisionFencedStore(store.other, parent.id, full.deployment.revision), "ETH-USD"
    )
    broker = _QuantityVenue()
    await maintain_open_inventory(
        await fresh.get_deployment(parent.id),
        strategy=_multi_strategy(),
        product=_product("ETH-USD"),
        candles=(_candle(),),
        broker=broker,
        store=fresh,
    )
    assert len(broker.submissions) == 1
    assert broker.submissions[0].product_id == "ETH-USD"
    assert broker.submissions[0].quantity == Decimal("1")


async def test_real_pg_stale_pending_cas_cannot_overwrite_flat_candidate_changes(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> None:
    """FLAT candidate's pending write loses to independent lifecycle/cash/latch revision."""
    first, second = engines
    writer, operator = PostgresExecutionStore(first), PostgresExecutionStore(second)
    parent, _orders = await _seed(writer, secondary=False)
    parent = await writer.save_deployment(replace(parent, phase=RuntimePhase.FLAT))
    before = await writer.get_accounting_snapshot(parent.id)
    latest = await operator.save_deployment(
        replace(
            before.deployment,
            cash=Decimal("950"),
            status=DeploymentStatus.STOPPED,
            lifecycle_command=LifecycleCommand.MANAGED_SHUTDOWN,
            daily_loss_latched=True,
        ),
        expected_revision=before.deployment.revision,
    )
    with pytest.raises(ExecutionConflictError, match="revision"):
        await writer.save_deployment(
            replace(before.deployment, phase=RuntimePhase.PENDING_ENTRY),
            expected_revision=before.deployment.revision,
        )
    after = await writer.get_accounting_snapshot(parent.id)
    assert after.deployment == latest


async def test_actual_pg_reuse_read_boundary_and_pending_cas(
    engines: tuple[AsyncEngine, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truly FLAT listed book learns a late canceled-entry fill between parent/child reads.

    Ordinary READ COMMITTED tears old cash with new children. Authoritative REPEATABLE
    READ stays consistent, and the later pending CAS must refuse the newer fill revision.
    This is not an ineligible OPEN listing followed by a convenient closing fill.
    """
    first, second = engines
    store, other = PostgresExecutionStore(first), PostgresExecutionStore(second)
    parent, orders = await _seed(store, secondary=False)
    for order in orders:
        await store.save_order(
            replace(order, status=OrderStatus.CANCELED, filled_quantity=Decimal(0))
        )
    await store.save_instrument_runtime(
        InstrumentRuntime(
            "BTC-USD",
            RuntimePhase.FLAT,
            pending_stop_price=Decimal("90"),
            pending_target_price=Decimal("120"),
        ),
        deployment_id=parent.id,
    )
    parent = await store.save_deployment(
        replace(
            parent,
            phase=RuntimePhase.FLAT,
            created_at=_TODAY - timedelta(hours=3),
            utc_day_open_at=_TODAY - timedelta(hours=3),
            venue_available_quote=Decimal("10000"),
        )
    )
    late_order = replace(orders[0], status=OrderStatus.CANCELED, filled_quantity=Decimal(0))
    original_snapshot = postgres_execution._snapshot
    raced = False
    candidate_children: list[int] = []

    async def late_fill_between_selects(
        connection: postgres_execution.AsyncConnection, deployment: Deployment
    ) -> DeploymentSnapshot:
        """Commit exact fill evidence on session two after candidate parent's SELECT on one."""
        nonlocal raced
        if connection.engine is first and not raced:
            raced = True
            await ingest_fill(
                await other.get_deployment(parent.id),
                fill=replace(_fill(late_order), filled_at=_TODAY - timedelta(hours=1)),
                order=late_order,
                store=other,
            )
            result = await original_snapshot(connection, deployment)
            candidate_children.append(len(result.fills))
            return result
        return await original_snapshot(connection, deployment)

    monkeypatch.setattr(postgres_execution, "_snapshot", late_fill_between_selects)
    monkeypatch.setattr("thytrader.execution.discretionary.utc_now", lambda: _TODAY)
    monkeypatch.setattr("thytrader.execution.mark_context.utc_now", lambda: _TODAY)
    monkeypatch.setattr("thytrader.execution.discretionary_book.utc_now", lambda: _TODAY)
    broker = _QuantityVenue()
    request = parse_discretionary_request(
        mode="live",
        product_id="BTC-USD",
        entry_kind="post_only_limit",
        quantity="0.01",
        limit_price="101",
        stop_price="90",
        take_profit_price="120",
        origin="agent",
        idempotency_key="pg-real-reuse",
        timeframe="1h",
    )
    with pytest.raises(ExecutionConflictError, match="revision"):
        await place_discretionary_order(
            store=store,
            broker=broker,
            market_data=_as_market_data(
                _PricedPreview(replace(_candle(), starts_at=_TODAY - timedelta(hours=1)))
            ),
            request=request,
            live_allowed=True,
            live_quote_cash=Decimal("10000"),
        )
    assert raced and candidate_children == [0]  # Repeatable observation, not torn children.
    loaded = await other.get_accounting_snapshot(parent.id)
    assert loaded.deployment.cash == Decimal("899.9")
    assert loaded.deployment.phase is RuntimePhase.OPEN
    assert loaded.deployment.revision == parent.revision + 1
    assert loaded.positions[0].quantity == Decimal(1)
    assert not broker.submissions
    assert len(loaded.intents) == 2  # Only the original canceled entries, no new intent.


class _PeerPgRaceStore(PostgresExecutionStore):
    """An independent session fills/stops a peer immediately before its pause CAS."""

    other: PostgresExecutionStore
    race_order: Order | None = None
    stop_peer = False

    async def save_breaker_pause(
        self,
        deployment_id: UUID,
        *,
        expected_revision: int,
        detail: str,
        daily_loss_latched: bool = False,
    ) -> Deployment:
        """Force a lost revision and verify retry preserves independently committed state."""
        order = self.race_order
        if order is not None and order.deployment_id == deployment_id:
            self.race_order = None
            filled = await ingest_fill(
                await self.other.get_deployment(deployment_id),
                fill=_fill(order),
                order=order,
                store=self.other,
            )
            await self.other.save_deployment(
                replace(
                    filled.snapshot.deployment,
                    drawdown_latched=True,
                    lifecycle_command=LifecycleCommand.MANAGED_SHUTDOWN
                    if self.stop_peer
                    else LifecycleCommand.STOP_NEW_ENTRIES,
                    status=DeploymentStatus.STOPPED if self.stop_peer else DeploymentStatus.RUNNING,
                ),
                expected_revision=filled.snapshot.deployment.revision,
            )
        return await super().save_breaker_pause(
            deployment_id,
            expected_revision=expected_revision,
            detail=detail,
            daily_loss_latched=daily_loss_latched,
        )


@pytest.mark.parametrize("stop_peer", [False, True])
async def test_actual_pg_scoped_quote_breaker_preserves_peer_fill_and_intent(
    engines: tuple[AsyncEngine, AsyncEngine], monkeypatch: pytest.MonkeyPatch, stop_peer: bool
) -> None:
    """Actual breaker caller forwards neutral per-peer CAS, not source product/lease authority."""
    first, second = engines
    store = _PeerPgRaceStore(first)
    store.other = PostgresExecutionStore(second)
    source = sibling_loss(live=True)
    source = replace(
        source,
        deployment=replace(
            source.deployment,
            kind=DeploymentKind.DISCRETIONARY,
            strategy_id=None,
            strategy_fingerprint=None,
            timeframe="1h",
        ),
    )
    await store.create_deployment(source.deployment)
    for order in source.orders:
        await store.save_intent(
            OrderIntent(
                id=order.intent_id,
                deployment_id=order.deployment_id,
                client_order_id=order.client_order_id,
                purpose=IntentPurpose.ENTRY if order.side is OrderSide.BUY else IntentPurpose.STOP,
                side=order.side,
                kind=order.kind,
                quantity=order.quantity,
                price=order.price,
                created_at=order.created_at,
                candle_starts_at=order.created_at,
                product_id=order.product_id,
            )
        )
        await store.save_order(order)
    for fill in source.fills:
        await store.save_fill(fill)
    for runtime in source.instrument_runtimes:
        await store.save_instrument_runtime(runtime, deployment_id=source.deployment.id)
    parent, orders = await _seed(store, secondary=True)
    for order in orders:
        await store.save_order(replace(order, status=OrderStatus.OPEN, filled_quantity=Decimal(0)))
    store.race_order = replace(orders[1], status=OrderStatus.OPEN, filled_quantity=Decimal(0))
    store.stop_peer = stop_peer
    before = await store.get_accounting_snapshot(parent.id)
    scoped = InstrumentScopedStore(
        RevisionFencedStore(store, source.deployment.id, source.deployment.revision), "BTC-USD"
    )
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    await _apply_circuit_breakers(
        await scoped.get_deployment(source.deployment.id),
        candle=_candle(),
        product_id="BTC-USD",
        store=scoped,
        risk_policy=_policy(),
        portfolio=(source, before),
        marks={"BTC-USD": Decimal("100")},
    )
    after = await store.other.get_accounting_snapshot(parent.id)
    assert after.deployment.cash == Decimal("899.9")
    assert after.deployment.drawdown_latched
    assert after.deployment.status is (
        DeploymentStatus.STOPPED if stop_peer else DeploymentStatus.PAUSED
    )
    assert after.deployment.lifecycle_command is (
        LifecycleCommand.MANAGED_SHUTDOWN if stop_peer else LifecycleCommand.STOP_NEW_ENTRIES
    )
    assert after.deployment.revision == parent.revision + (2 if stop_peer else 3)
    assert after.positions[0].product_id == "ETH-USD"
    assert (
        next(r for r in after.instrument_runtimes if r.product_id == "ETH-USD").phase
        is RuntimePhase.OPEN
    )
    assert (
        next(r for r in after.instrument_runtimes if r.product_id == "BTC-USD").phase
        is RuntimePhase.PENDING_ENTRY
    )


async def test_concurrent_exact_fill_observation_is_locally_idempotent(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> None:
    """Independent engines applying the same exact venue fill advance cash/revision once."""
    first, second = engines
    store, other = PostgresExecutionStore(first), PostgresExecutionStore(second)
    parent, orders = await _seed(store, secondary=False)
    fill = _fill(orders[0])
    before = await store.get_accounting_snapshot(parent.id)
    results = await asyncio.gather(
        ingest_fill(before, fill=fill, order=orders[0], store=store),
        ingest_fill(before, fill=replace(fill, id=uuid4()), order=orders[0], store=other),
    )
    assert sum(result.applied for result in results) == 1
    after = await other.get_accounting_snapshot(parent.id)
    assert after.deployment.cash == Decimal("899.9")
    assert after.deployment.revision == parent.revision + 1
    assert len(after.fills) == 1 and after.positions[0].quantity == Decimal(1)


@pytest.mark.parametrize("scope_outer", [True, False])
async def test_pg_refreshed_fence_rejects_old_snapshot_in_both_wrapper_orders(
    engines: tuple[AsyncEngine, AsyncEngine], scope_outer: bool
) -> None:
    """A wrapper's fill refresh does not bless old cash/runtime on a subsequent caller save."""
    first, second = engines
    store = PostgresExecutionStore(first)
    parent, orders = await _seed(store, secondary=True)
    scope = (
        InstrumentScopedStore(RevisionFencedStore(store, parent.id, parent.revision), "ETH-USD")
        if scope_outer
        else RevisionFencedStore(
            InstrumentScopedStore(store, "ETH-USD"), parent.id, parent.revision
        )
    )
    before = await scope.get_deployment(parent.id)
    await ingest_fill(before, fill=_fill(orders[1]), order=orders[1], store=scope)
    reload_store = PostgresExecutionStore(second)
    committed = await reload_store.get_accounting_snapshot(parent.id)
    with pytest.raises(ExecutionConflictError, match="revision"):
        await scope.save_deployment(before.deployment)
    assert await reload_store.get_accounting_snapshot(parent.id) == committed


async def test_pg_runtime_write_failure_rolls_back_the_parent(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> None:
    """A real product-column length violation after parent UPDATE still commits no effects."""
    first, second = engines
    store = PostgresExecutionStore(first)
    parent, _orders = await _seed(store, secondary=False)
    before = await store.get_accounting_snapshot(parent.id)
    with pytest.raises(ExecutionStoreError):
        await store.save_deployment(
            replace(parent, cash=Decimal("7")),
            expected_revision=parent.revision,
            instrument_runtime=InstrumentRuntime("X" * 256, RuntimePhase.FLAT),
        )
    assert await PostgresExecutionStore(second).get_accounting_snapshot(parent.id) == before


@pytest.mark.parametrize("secondary", [False, True])
async def test_pg_plain_save_mirrors_only_a_single_product_overlay(
    engines: tuple[AsyncEngine, AsyncEngine], secondary: bool
) -> None:
    """The deployment row owns a single-product book's runtime; multi-product rows stay."""
    first, second = engines
    store = PostgresExecutionStore(first)
    parent, _orders = await _seed(store, secondary=secondary)
    current = (await store.get_deployment(parent.id)).deployment
    bar = current.created_at.replace(microsecond=0)
    await store.save_deployment(
        replace(current, phase=RuntimePhase.OPEN, last_evaluated_bar=bar, bars_held=3),
        expected_revision=current.revision,
    )
    rows = {
        row.product_id: row
        for row in (
            await PostgresExecutionStore(second).get_accounting_snapshot(parent.id)
        ).instrument_runtimes
    }
    primary = rows["BTC-USD"]
    if secondary:
        assert (primary.phase, primary.last_evaluated_bar, primary.bars_held) == (
            RuntimePhase.PENDING_ENTRY,
            None,
            0,
        )
        assert rows["ETH-USD"].phase is RuntimePhase.PENDING_ENTRY
    else:
        assert (primary.phase, primary.last_evaluated_bar, primary.bars_held) == (
            RuntimePhase.OPEN,
            bar,
            3,
        )
        assert set(rows) == {"BTC-USD"}
