"""Live funding accrual uses the existing atomic ledger and verified daily opening source."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_paper_futures_books import (
    _START,
    _binding,
    _MarketData,
    _Observations,
    _record,
    _strategy,
)
from thytrader.execution.futures_paper import (
    FuturesRuntime,
    futures_runtime_scope,
    prepare_futures_book,
)
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from thytrader.market_data.service import MarketDataService


@pytest.mark.anyio
async def test_live_funding_accrues_once_across_restart_and_keeps_cash_ledger() -> None:
    """Settled rate history supplies modeled hourly accrual, not a fabricated venue settlement."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    bindings = InMemoryFuturesContractStore()
    await bindings.bind_contract(_binding(snapshot))
    records = tuple(_record(_START + timedelta(hours=hour), "0.001") for hour in (32, 33))
    original_cash = snapshot.deployment.cash
    state = None
    for _ in range(2):
        with futures_runtime_scope(FuturesRuntime(bindings, _Observations(records))):
            state, snapshot = await prepare_futures_book(
                snapshot,
                strategy=_strategy(),
                store=store,
                market_data=cast("MarketDataService", _MarketData()),
                now=_START + timedelta(hours=33),
            )
        snapshot = await store.get_deployment(snapshot.deployment.id)
    assert state is not None and state.funding_overdue is None
    assert len(snapshot.funding) == 2
    assert all(flow.applied_at is not None for flow in snapshot.funding)
    assert snapshot.deployment.cash == original_cash + sum(flow.amount for flow in snapshot.funding)
    opening = reconstruct_day_open(snapshot, as_of=_START + timedelta(hours=34))
    assert opening is not None
    assert opening.source == "per_product_applied_fills_and_funding_v1"


@pytest.mark.anyio
@pytest.mark.parametrize("rate", ["NaN", "sNaN", "Infinity", "1E+999999999"])
async def test_invalid_live_funding_never_poison_cash_or_claim_settlement(rate: str) -> None:
    """Malformed/nonrepresentable accrual becomes missing evidence, not a stored number."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    before = snapshot.deployment.cash
    bindings = InMemoryFuturesContractStore()
    await bindings.bind_contract(_binding(snapshot))
    hour = _START + timedelta(hours=32)
    with futures_runtime_scope(FuturesRuntime(bindings, _Observations((_record(hour, rate),)))):
        state, current = await prepare_futures_book(
            snapshot,
            strategy=_strategy(),
            store=store,
            market_data=cast("MarketDataService", _MarketData()),
            now=_START + timedelta(hours=34),
        )
    assert state is not None and state.funding_overdue == hour
    assert current.funding == ()
    assert current.deployment.cash == before
