"""Cycle-shared live futures reads with no spot-only account traffic."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_futures_venue_check import evidence
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _PERP, _START, _book, _strategy
from thytrader.execution.futures_live import (
    FuturesLiveAccount,
    FuturesLiveRuntime,
    admission_venue,
    live_futures_runtime_scope,
    prepare_live_futures_book,
)
from thytrader.market_data.cycle_reads import CycleReads, cycle_reads_scope
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode


class Source:
    """Explicit fake runtime capability, not approval for the real venue."""

    def __init__(self) -> None:
        """Count complete synthetic account reads."""
        self.reads = 0

    async def read_account(
        self,
        managed_client_ids: frozenset[tuple[str, str]],
        product_ids: frozenset[str] = frozenset(),
    ) -> FuturesLiveAccount:
        """Return synthetic facts with a unique observation time."""
        del managed_client_ids, product_ids
        self.reads += 1
        return FuturesLiveAccount(
            venue=replace(evidence(Decimal(0)), observed_at=_START + timedelta(seconds=self.reads)),
            marks={_PERP: Decimal(100)},
            available_margin_usd=Decimal(100),
            liquidation_threshold_usd=Decimal(20),
        )


@pytest.mark.anyio
async def test_account_shared_once_per_cycle_only_for_live_futures() -> None:
    """Multiple books share one account observation; other modes and products read nothing."""
    store = InMemoryExecutionStore()
    snapshot = await _book(store, _strategy())
    live = replace(snapshot, deployment=replace(snapshot.deployment, mode=DeploymentMode.LIVE))
    sibling = await _book(store, _strategy())
    sibling = replace(
        sibling,
        deployment=replace(
            sibling.deployment, mode=DeploymentMode.LIVE, product_id="ETP-20DEC30-CDE"
        ),
    )
    source = Source()
    runtime = FuturesLiveRuntime(source)
    with live_futures_runtime_scope(runtime), cycle_reads_scope(CycleReads()):
        assert await prepare_live_futures_book(snapshot, portfolio=(snapshot,)) is None
        spot = replace(live, deployment=replace(live.deployment, product_id="BTC-USD"))
        assert await prepare_live_futures_book(spot, portfolio=(spot,)) is None
        assert source.reads == 0
        one = await prepare_live_futures_book(live, portfolio=(live,))
        two = await prepare_live_futures_book(sibling, portfolio=(live, sibling))
        assert one is two
        assert source.reads == 1
    with live_futures_runtime_scope(runtime), cycle_reads_scope(CycleReads()):
        three = await prepare_live_futures_book(live, portfolio=(live,))
        assert three is not one
        assert source.reads == 2


@pytest.mark.anyio
async def test_new_entry_since_shared_observation_invalidates_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle sharing cannot spend the same pre-submit buying power on a second book."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    before = await venue.read_account(frozenset())
    assert admission_venue(before, ()) is before.venue
    snapshot = await open_live_book(InMemoryExecutionStore(), venue)
    assert admission_venue(before, (snapshot,)) is None
    fresh = await venue.read_account(frozenset())
    assert admission_venue(fresh, (snapshot,)) is fresh.venue
