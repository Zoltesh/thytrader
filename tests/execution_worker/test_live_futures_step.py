"""Actual worker steps supervise injected live futures before fetching decision bars."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.decision_support import Catalog
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import FuturesFees, clock
from tests.execution.test_paper_futures_books import (
    _PERP,
    _binding,
    _book,
    _candles,
    _Observations,
    _product,
    _record,
    _strategy,
)
from tests.worker_patching import patch_worker_global
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    OrderStatus,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


@pytest.mark.anyio
@pytest.mark.parametrize("flatten", [False, True])
async def test_worker_stopped_step_reduces_without_candles(
    monkeypatch: MonkeyPatch, flatten: bool
) -> None:
    """Both explicit flatten and managed-stop margin breach use the real fenced worker step."""
    venue = FakeVenue()
    monkeypatch.setattr("thytrader.execution_worker.futures_step.utc_now", lambda: venue.now)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await store.save_deployment(
        replace(
            snapshot.deployment,
            status=DeploymentStatus.STOPPED,
            lifecycle_command=LifecycleCommand.FLATTEN
            if flatten
            else LifecycleCommand.STOP_NEW_ENTRIES,
        )
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    if not flatten:
        venue.margin = Decimal(1)
    policy = compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(live_enabled=False, live_derisk_margin_ratio="1.5")}
    )
    with (
        futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        await _process_one(
            deployment_id=snapshot.deployment.id,
            store=store,
            publication_store=Catalog(_strategy()),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=venue.broker(),
            quote_reader=None,
            risk_policy=policy,
            portfolio=(snapshot,),
            user_feed_store=None,
            memory_store=None,
        )
    current = await store.get_deployment(snapshot.deployment.id)
    assert current.position is None
    assert venue.net == 0
    expected = IntentPurpose.STOP if flatten else IntentPurpose.LIQUIDATION
    assert any(intent.purpose is expected for intent in current.intents)


@pytest.mark.anyio
async def test_running_worker_short_never_reads_spot_base(monkeypatch: MonkeyPatch) -> None:
    """The actual strategy worker passes futures margin evidence through entry admission."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    payload = _strategy().model_dump(mode="python")
    payload["entry"]["side"] = "short"
    payload["sizing"]["min_quote_notional"] = "0.1"
    payload["sizing"]["max_quote_notional"] = "2.6"
    strategy = StrategyDefinition.model_validate(payload)
    store = InMemoryExecutionStore()
    snapshot = await _book(store, strategy)
    await store.save_deployment(
        replace(
            snapshot.deployment,
            mode=DeploymentMode.LIVE,
            cash=Decimal(0),
            initial_equity=Decimal(0),
            allocated_capital=Decimal(100),
            performance_capital_quote=Decimal(100),
        )
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))

    async def window(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Supply a deterministic closed window; execution and accounting stay real."""
        del args, kwargs
        return _product(), _candles(31), _candles(31)[-1].starts_at

    patch_worker_global(monkeypatch, "_closed_window", window)
    policy = compiled_default_risk_policy().model_copy(
        update={
            "futures": FuturesRiskPolicy(
                live_enabled=True,
                live_capital_usd="1000",
                product_allowlist=(_PERP,),
                max_order_contracts=2,
                live_spot_collateral_reserve_quote="1000",
            )
        }
    )
    with (
        futures_runtime_scope(
            FuturesRuntime(
                contracts, _Observations((_record(venue.now - timedelta(hours=1), "0.001"),))
            )
        ),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        await _process_one(
            deployment_id=snapshot.deployment.id,
            store=store,
            publication_store=Catalog(strategy),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=venue.broker(),
            quote_reader=FuturesFees(),
            risk_policy=policy,
            portfolio=(snapshot,),
            user_feed_store=None,
            memory_store=None,
        )
    current = await store.get_deployment(snapshot.deployment.id)
    assert len(current.orders) == 1
    assert current.orders[0].status is OrderStatus.OPEN
