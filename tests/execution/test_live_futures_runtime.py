"""Injected live futures runtime; none of these fixtures asserts venue acceptance."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from tests.execution.test_live_bracket import _RecordingBroker
from tests.execution.test_paper_futures_books import (
    _START,
    _binding,
    _book,
    _MarketData,
    _Observations,
    _strategy,
)
from thytrader.execution.futures_paper import (
    FuturesRuntime,
    futures_runtime_scope,
    prepare_futures_book,
)
from thytrader.execution.submit import submit_intent
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, IntentPurpose, OrderKind, OrderSide

if TYPE_CHECKING:
    from thytrader.market_data.service import MarketDataService


@pytest.mark.anyio
async def test_live_preparation_loads_binding_margin_and_funding() -> None:
    """A pre-existing live book receives bound terms without enabling a start surface."""
    store = InMemoryExecutionStore()
    snapshot = await _book(store, _strategy())
    await store.save_deployment(
        replace(snapshot.deployment, mode=DeploymentMode.LIVE, cash=Decimal(0))
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    with futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))):
        state, _ = await prepare_futures_book(
            snapshot,
            strategy=_strategy(),
            store=store,
            market_data=cast("MarketDataService", _MarketData()),
            now=_START + timedelta(hours=1),
        )
    assert state is not None
    assert state.binding == _binding(snapshot)
    assert state.margin is not None
    assert state.margin.contract_size == Decimal("0.01")


@pytest.mark.anyio
async def test_price_free_live_reduction_persists_explicit_decision_instant() -> None:
    """A reduction has no candle and no order price, not a fabricated executable bar."""
    store = InMemoryExecutionStore()
    snapshot = await _book(store, _strategy())
    await store.save_deployment(replace(snapshot.deployment, mode=DeploymentMode.LIVE))
    broker = _RecordingBroker()
    await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=snapshot.deployment.product_id,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.01"),
        price=None,
        candle=None,
        decision_at=_START,
    )
    current = await store.get_deployment(snapshot.deployment.id)
    assert current.intents[0].candle_starts_at == _START
    assert current.orders[0].price is None
    assert len(broker.placed) == 1
