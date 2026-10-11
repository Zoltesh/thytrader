"""Disposable PostgreSQL verifies live futures fill, repair and funding atomic boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING, cast

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import (
    _PERP,
    _START,
    _binding,
    _candles,
    _MarketData,
    _Observations,
    _record,
    _strategy,
)
from tests.persistence.test_postgres_futures_books import _deployment, _run
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import (
    FuturesRuntime,
    futures_runtime_scope,
    prepare_futures_book,
)
from thytrader.execution.futures_position_supervision import enforce_position_check
from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.execution.leases import RevisionFencedStore
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_futures_books import PostgresFuturesContractStore
from thytrader.trading.models import (
    DeploymentMode,
    ExecutionConflictError,
    IntentPurpose,
    OrderKind,
    OrderSide,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.models import DeploymentSnapshot

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="Disposable PostgreSQL integration URL required.",
)


async def _open(
    engine: AsyncEngine, venue: FakeVenue
) -> tuple[PostgresExecutionStore, DeploymentSnapshot]:
    """Seed a dormant test book, then use real intent and atomic REST-fill ingestion."""
    store = PostgresExecutionStore(engine)
    deployment = replace(
        _deployment(),
        mode=DeploymentMode.LIVE,
        paper_starting_cash=None,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        cash=Decimal(0),
        initial_equity=Decimal(0),
        allocated_capital=Decimal(100),
        pending_stop_price=Decimal(90),
        created_at=_START,
    )
    await store.create_deployment(deployment)
    order = await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=deployment.id,
        product_id=_PERP,
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.02"),
        price=venue.price,
        candle=_candles(31)[-1],
    )
    venue.fill(order.venue_order_id or "")
    venue.advance()
    snapshot = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue.broker(), store=store
    )
    assert snapshot.position is not None
    return store, snapshot


def test_live_reduction_and_restart_fill_economics_round_trip() -> None:
    """A fresh store/adapter recovers an ambiguous exit exactly once under a revision fence."""

    async def body(engine: AsyncEngine) -> None:
        venue = FakeVenue()
        store, snapshot = await _open(engine, venue)
        venue.timeout_after_post = True
        fenced = RevisionFencedStore(store, snapshot.deployment.id, snapshot.deployment.revision)
        await reduce_live_futures_position(
            snapshot,
            account=await venue.read_account(frozenset()),
            broker=venue.broker(),
            store=fenced,
            purpose=IntentPurpose.STOP,
            now=venue.now,
        )
        restarted = PostgresExecutionStore(engine)
        after = await restarted.get_deployment(snapshot.deployment.id)
        assert after.position is None
        assert after.deployment.cash == Decimal("-0.30")
        assert len(after.fills) == 2
        assert all(fill.economics_applied_at is not None for fill in after.fills)
        await reduce_live_futures_position(
            after,
            account=await venue.read_account(frozenset()),
            broker=venue.broker(),
            store=restarted,
            purpose=IntentPurpose.STOP,
            now=venue.now,
        )
        assert len(venue.creates) == 2

    _run(body)


def test_proven_excess_repair_preserves_projection_atomically() -> None:
    """A real PostgreSQL fill transaction applies repair cash without consuming book inventory."""

    async def body(engine: AsyncEngine) -> None:
        venue = FakeVenue()
        store, snapshot = await _open(engine, venue)
        assert snapshot.position is not None
        projection = replace(snapshot.position, quantity=Decimal("0.01"))
        await store.save_position(projection, deployment_id=snapshot.deployment.id)
        snapshot = await store.get_deployment(snapshot.deployment.id)
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            for _ in range(2):
                venue.advance()
                snapshot = await enforce_position_check(
                    snapshot,
                    account=await venue.read_account(frozenset()),
                    broker=venue.broker(),
                    store=store,
                    now=venue.now,
                )
        reloaded = await PostgresExecutionStore(engine).get_deployment(snapshot.deployment.id)
        assert reloaded.position == projection
        assert venue.net == 1
        assert reloaded.deployment.cash == Decimal("-1.60")
        assert len(reloaded.fills) == 2
        assert all(fill.economics_applied_at is not None for fill in reloaded.fills)

    _run(body)


def test_live_funding_restart_idempotence_and_stale_fence() -> None:
    """Funding retains the existing unique hour and revision-checked atomic cash update."""

    async def body(engine: AsyncEngine) -> None:
        venue = FakeVenue()
        store, snapshot = await _open(engine, venue)
        contracts = PostgresFuturesContractStore(engine)
        await contracts.bind_contract(_binding(snapshot))
        records = (_record(_START + timedelta(hours=32), "0.001"),)
        before = snapshot.deployment.cash
        stale = RevisionFencedStore(store, snapshot.deployment.id, snapshot.deployment.revision)
        with futures_runtime_scope(FuturesRuntime(contracts, _Observations(records))):
            for _ in range(2):
                _, snapshot = await prepare_futures_book(
                    snapshot,
                    strategy=_strategy(),
                    store=PostgresExecutionStore(engine),
                    market_data=cast("MarketDataService", _MarketData()),
                    now=_START + timedelta(hours=32),
                )
        assert len(snapshot.funding) == 1
        assert snapshot.deployment.cash == before + snapshot.funding[0].amount
        with pytest.raises(ExecutionConflictError):
            await stale.save_deployment(snapshot.deployment)
        assert (await store.get_deployment(snapshot.deployment.id)).funding == snapshot.funding

    _run(body)
