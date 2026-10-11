"""A stopped futures flatten waits for fresh venue-flat confirmation before retiring reads."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.execution.decision_support import Catalog
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _binding, _Observations, _strategy
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, LifecycleCommand


@pytest.mark.anyio
async def test_flatten_requires_later_flat_sample_then_stops_account_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical fill is not a new CFM position read; old evidence cannot settle I9."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await store.save_deployment(
        replace(
            snapshot.deployment,
            status=DeploymentStatus.STOPPED,
            lifecycle_command=LifecycleCommand.FLATTEN,
        )
    )
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))

    async def step() -> None:
        """Exercise the actual fenced stopped worker path."""
        current = await store.get_deployment(snapshot.deployment.id)
        await _process_one(
            deployment_id=snapshot.deployment.id,
            store=store,
            publication_store=Catalog(_strategy()),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=venue.broker(),
            quote_reader=None,
            risk_policy=compiled_default_risk_policy(),
            portfolio=(current,),
            user_feed_store=None,
            memory_store=None,
        )

    with (
        futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        await step()
        pending = await store.get_deployment(snapshot.deployment.id)
        assert pending.position is None
        assert (pending.deployment.mismatch_detail or "").startswith(
            "FUTURES_FLAT_CONFIRMATION_PENDING"
        )
        venue.advance()
        await step()
        complete = await store.get_deployment(snapshot.deployment.id)
        assert complete.deployment.mismatch_detail is None
        reads = venue.account_reads
        await step()
        assert venue.account_reads == reads
