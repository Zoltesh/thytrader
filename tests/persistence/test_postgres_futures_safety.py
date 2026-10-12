"""PostgreSQL durability for futures safety duties, including real-worker crash recovery."""

from __future__ import annotations

from dataclasses import replace
import os
from typing import TYPE_CHECKING

import pytest

from tests.execution.test_live_futures_entry import clock
from tests.execution_worker.futures_safety_support import closes, protect, step, stop_book
from tests.execution_worker.test_futures_safety_crashes import ProcessLoss
from tests.execution_worker.test_futures_safety_obligations import TransientReadVenue
from tests.persistence.test_postgres_futures_books import _run
from tests.persistence.test_postgres_live_futures import _open
from thytrader.execution.futures_live import (
    FuturesLiveRuntime,
    live_futures_runtime_scope,
    managed_live_futures,
)
from thytrader.execution.leases import RevisionFencedStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.models import (
    DeploymentSnapshot,
    ExecutionConflictError,
    Fill,
    LifecycleCommand,
    Order,
    OrderKind,
)
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None, reason="Disposable PostgreSQL required."
)


def test_derisk_survives_store_recreation_faults_and_scoped_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both duties survive SQL roundtrip, unrelated faults and scoped writes; stale writes fail."""

    async def body(engine: AsyncEngine) -> None:
        venue = TransientReadVenue()
        clock(monkeypatch, venue)
        store, snapshot = await _open(engine, venue)
        snapshot, _ = await protect(store, venue, snapshot)
        await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
        venue.margin = venue.threshold / 2
        venue.cancel_confirmed = False
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            pending = await step(store, venue, snapshot.deployment.id)
        assert pending.deployment.futures_derisk_pending
        assert pending.deployment.futures_flat_confirmation_after is not None
        stale = RevisionFencedStore(store, pending.deployment.id, pending.deployment.revision)
        scoped = InstrumentScopedStore(
            PostgresExecutionStore(engine), pending.deployment.product_id
        )
        await scoped.save_deployment(
            replace(pending.deployment, mismatch_detail="synthetic unrelated fault")
        )
        with pytest.raises(ExecutionConflictError):
            await stale.save_deployment(
                replace(
                    pending.deployment,
                    futures_derisk_pending=False,
                    futures_flat_confirmation_after=None,
                )
            )
        reloaded = await PostgresExecutionStore(engine).get_deployment(pending.deployment.id)
        assert reloaded.deployment.futures_derisk_pending
        assert (
            reloaded.deployment.futures_flat_confirmation_after
            == pending.deployment.futures_flat_confirmation_after
        )
        venue.margin = venue.threshold * 10
        venue.cancel_confirmed = True
        venue.fail_order_read = True
        for _ in range(4):
            venue.advance()
            with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
                current = await step(PostgresExecutionStore(engine), venue, snapshot.deployment.id)
        assert current.position is None and venue.net == 0 and closes(venue) == 1
        assert not current.deployment.futures_derisk_pending
        assert current.deployment.futures_flat_confirmation_after is None

    _run(body)


@pytest.mark.parametrize("natural_stop", [False, True])
def test_postgres_close_fill_crash_retains_flat_read_duty(
    monkeypatch: pytest.MonkeyPatch, natural_stop: bool
) -> None:
    """Interrupt after the actual SQL fill commit and recover with a fresh store and adapter."""

    async def body(engine: AsyncEngine) -> None:
        venue = TransientReadVenue()
        clock(monkeypatch, venue)
        store, snapshot = await _open(engine, venue)
        if natural_stop:
            snapshot, protection = await protect(store, venue, snapshot)
            await stop_book(store, snapshot)
            assert protection.venue_order_id is not None
            venue.fill(protection.venue_order_id)
        else:
            await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
        original = PostgresExecutionStore.apply_fill_transaction
        crashed = False

        async def interrupt(
            self: PostgresExecutionStore,
            deployment_id: UUID,
            *,
            fill: Fill,
            order: Order,
            cooldown_bars: int = 0,
            timeframe: str | None = None,
        ) -> tuple[bool, DeploymentSnapshot]:
            """Preserve the committed SQL transaction before simulating process death."""
            nonlocal crashed
            result = await original(
                self,
                deployment_id,
                fill=fill,
                order=order,
                cooldown_bars=cooldown_bars,
                timeframe=timeframe,
            )
            expected_kind = OrderKind.STOP_LIMIT if natural_stop else OrderKind.MARKETABLE
            if order.kind is expected_kind and not crashed:
                crashed = True
                raise ProcessLoss("after SQL fill commit")
            return result

        monkeypatch.setattr(PostgresExecutionStore, "apply_fill_transaction", interrupt)
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)), pytest.raises(ProcessLoss):
            await step(store, venue, snapshot.deployment.id)
        pending = await PostgresExecutionStore(engine).get_deployment(snapshot.deployment.id)
        assert pending.position is None
        assert all(fill.economics_applied_at is not None for fill in pending.fills)
        assert pending.deployment.futures_flat_confirmation_after is not None
        reads = venue.account_reads
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            same = await step(PostgresExecutionStore(engine), venue, snapshot.deployment.id)
        assert venue.account_reads > reads and managed_live_futures(same)
        venue.advance()
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            confirmed = await step(PostgresExecutionStore(engine), venue, snapshot.deployment.id)
        assert not managed_live_futures(confirmed)
        assert closes(venue) == (0 if natural_stop else 1)

    _run(body)
