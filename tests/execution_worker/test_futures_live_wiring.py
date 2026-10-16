"""Production wiring of the live futures broker (ADR 0134 P2-7).

The worker's venue builder composes a product-routed futures broker on every credentialed
generation. Spot books keep the plain spot adapter (its REST calls are unchanged), and only a
live futures book is handed the routed broker; without one it gets no broker at all.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_paper_futures_books import _book, _strategy
from tests.execution_worker.test_futures_source import _Observations
from tests.execution_worker.test_venue_reload import _KEY, _PEM, _settings
from thytrader.credentials.service import settings_with_coinbase
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.execution.futures_sources import RecordedFuturesSource
from thytrader.execution.paper import PaperBroker
from thytrader.execution.stopped import STOPPED_BROKER_UNAVAILABLE
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.execution_worker import service
from thytrader.execution_worker.futures_step import (
    LIVE_FUTURES_BROKER_UNAVAILABLE,
    supervised_live_futures,
)
from thytrader.execution_worker.futures_venue import coinbase_futures_extension
from thytrader.execution_worker.venue import book_live_broker, build_execution_venue
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, DeploymentStatus, LifecycleCommand

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.execution.broker import Broker


def _extension() -> Any:
    """The production extension over inert stores."""
    return coinbase_futures_extension(observations=_Observations(()), mirror=None)


def test_credentialed_generation_composes_a_routed_futures_broker(tmp_path: Path) -> None:
    """The spot slot keeps the spot adapter; the futures slot and evidence source are added."""
    settings = settings_with_coinbase(_settings(tmp_path), key_name=_KEY, private_key=_PEM)
    venue = build_execution_venue(settings, 1, extend=_extension())
    assert isinstance(venue.live_broker, CoinbaseRestBroker)
    assert isinstance(venue.futures_live_broker, ProductRoutedBroker)
    assert venue.futures_runtime is not None
    assert isinstance(venue.futures_runtime.source, RecordedFuturesSource)


def test_without_credentials_nothing_is_composed(tmp_path: Path) -> None:
    """A demo generation has neither broker; the extension never runs."""
    venue = build_execution_venue(_settings(tmp_path), 0, extend=_extension())
    assert venue.live_broker is None
    assert venue.futures_live_broker is None
    assert venue.futures_runtime is None


@pytest.mark.parametrize(
    ("product_id", "mode", "expected"),
    [
        ("BTC-USDC", DeploymentMode.LIVE, "spot"),
        ("BTC-USDC", DeploymentMode.PAPER, "spot"),
        ("BIP-20DEC30-CDE", DeploymentMode.PAPER, "spot"),
        ("BIP-20DEC30-CDE", DeploymentMode.LIVE, "futures"),
    ],
)
@pytest.mark.anyio
async def test_only_a_live_futures_book_gets_the_routed_broker(
    product_id: str, mode: DeploymentMode, expected: str
) -> None:
    """Selection is by the book's product and mode, never by a fallback to spot."""
    store = InMemoryExecutionStore()
    deployment = replace(
        (await _book(store, _strategy())).deployment, product_id=product_id, mode=mode
    )
    spot, futures = PaperBroker(), PaperBroker()
    chosen = book_live_broker(deployment, spot=spot, futures=futures)
    assert chosen is (futures if expected == "futures" else spot)
    if expected == "futures":
        assert book_live_broker(deployment, spot=spot, futures=None) is None


@pytest.mark.anyio
async def test_the_cycle_hands_each_book_its_own_live_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One cycle: the live spot book gets the spot broker, the live futures book the router."""
    store = InMemoryExecutionStore()
    futures_book = (await _book(store, _strategy())).deployment
    await store.save_deployment(replace(futures_book, mode=DeploymentMode.LIVE))
    spot_book = await store.create_deployment(
        replace(futures_book, id=uuid4(), product_id="BTC-USDC", mode=DeploymentMode.LIVE)
    )
    seen: dict[Any, Broker | None] = {}

    async def record(*, deployment_id: Any, live_broker: Broker | None, **_: Any) -> None:
        seen[deployment_id] = live_broker

    monkeypatch.setattr(service, "_process_one", record)
    spot, futures = PaperBroker(), PaperBroker()
    await service._run_cycle(
        store=store,
        publication_store=DisabledStrategySnapshotStore(),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=spot,
        futures_live_broker=futures,
        quote_reader=None,
        risk_store=None,
    )
    assert seen[spot_book.id] is spot
    assert seen[futures_book.id] is futures


def test_a_failing_futures_extension_keeps_the_spot_generation(tmp_path: Path) -> None:
    """Futures composition can never take down live spot: the generation keeps spot only."""

    def broken(*_: Any) -> Any:
        raise RuntimeError("synthetic futures composition fault")

    settings = settings_with_coinbase(_settings(tmp_path), key_name=_KEY, private_key=_PEM)
    venue = build_execution_venue(settings, 4, extend=broken)
    assert isinstance(venue.live_broker, CoinbaseRestBroker)
    assert venue.futures_live_broker is None
    assert venue.futures_runtime is None


def test_the_router_wraps_the_generation_spot_broker(tmp_path: Path) -> None:
    """The worker's partial builder composes on credentials; the router reuses the spot object."""
    settings = settings_with_coinbase(_settings(tmp_path), key_name=_KEY, private_key=_PEM)
    venue = partial(build_execution_venue, extend=_extension())(settings, 5)
    assert isinstance(venue.futures_live_broker, ProductRoutedBroker)
    assert venue.futures_live_broker._spot is venue.live_broker


@pytest.mark.anyio
@pytest.mark.parametrize("stopped", [False, True])
async def test_a_live_futures_book_without_a_futures_broker_is_never_silently_skipped(
    stopped: bool,
) -> None:
    """Running (even with a pending de-risk) pauses; a stopped flatten is noted; duties stay."""
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, FakeVenue())
    deployment = replace(snapshot.deployment, futures_derisk_pending=True)
    if stopped:
        deployment = replace(
            deployment,
            status=DeploymentStatus.STOPPED,
            lifecycle_command=LifecycleCommand.FLATTEN,
        )
    await store.save_deployment(deployment)
    snapshot = await store.get_deployment(deployment.id)
    async with supervised_live_futures(
        snapshot,
        state=None,
        broker=None,
        store=store,
        policy=compiled_default_risk_policy(),
        portfolio=(snapshot,),
    ) as supervised:
        pass
    assert supervised.position is not None
    assert supervised.deployment.futures_derisk_pending
    if stopped:
        assert supervised.deployment.status is DeploymentStatus.STOPPED
        assert supervised.deployment.mismatch_detail == STOPPED_BROKER_UNAVAILABLE
    else:
        assert supervised.deployment.status is DeploymentStatus.PAUSED
        assert supervised.deployment.mismatch_detail == LIVE_FUTURES_BROKER_UNAVAILABLE
