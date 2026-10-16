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

from tests.execution.test_paper_futures_books import _book, _strategy
from tests.execution_worker.test_futures_source import _Observations
from tests.execution_worker.test_venue_reload import _KEY, _PEM, _settings
from thytrader.credentials.service import settings_with_coinbase
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.execution.futures_sources import RecordedFuturesSource
from thytrader.execution.paper import PaperBroker
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.execution_worker import service
from thytrader.execution_worker.futures_venue import coinbase_futures_extension
from thytrader.execution_worker.venue import book_live_broker, build_execution_venue
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode

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


def test_partial_builder_matches_the_worker_signature(tmp_path: Path) -> None:
    """The worker binds the extension with ``partial``; the builder stays (settings, generation)."""
    builder = partial(build_execution_venue, extend=_extension())
    assert builder(_settings(tmp_path), 2).generation == 2
