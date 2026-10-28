"""Staged process composition uses the real GET-only CFM adapter, never activates it."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from tests.exchanges.test_coinbase_cfm import _transport
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import (
    _PERP,
    _binding,
    _book,
    _Observations,
    _strategy,
)
from thytrader.config import Settings
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_cfm import (
    BALANCE_SUMMARY_PATH,
    POSITIONS_PATH,
    CoinbaseCfmAccount,
)
from thytrader.exchanges.futures_models import FuturesMarginWindow
from thytrader.exchanges.models import ExchangeOpenOrder
from thytrader.execution import futures_sources
from thytrader.execution.futures_paper import (
    FuturesRuntime,
    futures_runtime_scope,
    prepare_futures_book,
)
from thytrader.execution.futures_sources import CatalogContractSizes, RecordedFuturesSource
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.execution_worker.futures_venue import compose_futures_venue
from thytrader.execution_worker.venue import build_execution_venue
from thytrader.market_data.cycle_reads import CycleReads, cycle_reads_scope
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.market_data.futures_observations import FuturesInstrumentObservation


class NoExternalOrders:
    """Complete synthetic empty order evidence, not guessed absence."""

    async def list_futures_open_orders(self) -> tuple[()]:
        """No working orders in this fixture."""
        return ()


@pytest.mark.anyio
async def test_source_reads_two_cfm_paths_and_keeps_unknown_killswitch() -> None:
    """Mirror absence cannot assert a disabled killswitch; catalog units match broker units."""
    transport = _transport(positions=lambda: {"positions": []})
    observations = _Observations(())
    source = RecordedFuturesSource(
        CoinbaseCfmAccount(transport), NoExternalOrders(), observations, mirror=None
    )
    with cycle_reads_scope(CycleReads()):
        account = await source.read_account(frozenset(), frozenset({_PERP}))
        size = await CatalogContractSizes(observations).contract_size(_PERP)
    assert [path for path, _ in transport.calls] == [BALANCE_SUMMARY_PATH, POSITIONS_PATH]
    assert account.venue.positions == {}
    assert account.venue.killswitch_enabled is None
    assert account.venue.external_order_products == frozenset()
    assert size == account.venue.contract_sizes[_PERP] == Decimal("0.01")


def test_composition_adds_a_routed_futures_broker_and_keeps_the_spot_broker() -> None:
    """Spot books keep the unchanged spot adapter; only the futures slot is product-routed."""
    default = build_execution_venue(Settings(_env_file=None), 0)
    assert default.futures_runtime is None
    assert default.futures_live_broker is None
    assert default.live_broker is None
    fake = FakeVenue()
    spot_broker = CoinbaseRestBroker(fake)
    spot = replace(default, live_broker=spot_broker)
    staged = compose_futures_venue(spot, broker=fake.broker(), source=fake)
    assert staged.live_broker is spot_broker
    assert isinstance(staged.futures_live_broker, ProductRoutedBroker)
    assert staged.futures_runtime is not None
    assert staged.futures_runtime.source is fake
    assert spot.futures_runtime is None


@pytest.mark.anyio
async def test_order_ownership_requires_exact_product_and_client_identity() -> None:
    """A managed client id echoed on another product cannot hide an external order."""

    class Orders:
        """Return one synthetic working order."""

        async def list_futures_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
            """The product is authoritative, not inferred from a client-id prefix."""
            return (
                ExchangeOpenOrder(
                    venue_order_id="test-order", product_id=_PERP, client_order_id="test-client"
                ),
            )

    source = RecordedFuturesSource(
        CoinbaseCfmAccount(_transport(positions=lambda: {"positions": []})),
        Orders(),
        _Observations(()),
        mirror=None,
    )
    own = await source.read_account(frozenset({(_PERP, "test-client")}), frozenset({_PERP}))
    foreign = await source.read_account(
        frozenset({("ETP-20DEC30-CDE", "test-client")}), frozenset({_PERP})
    )
    assert own.venue.external_order_products == frozenset()
    assert foreign.venue.external_order_products == frozenset({_PERP})


@pytest.mark.anyio
async def test_preparation_and_broker_share_one_catalog_observation() -> None:
    """The book's margin terms and venue contract conversion cannot mix catalog generations."""

    class Counting(_Observations):
        """Count catalog reads independently of account reads."""

        reads = 0

        async def latest_instrument(
            self, product_id: str
        ) -> tuple[FuturesInstrumentObservation, datetime] | None:
            """Supply the ordinary recorded fixture and count actual lookups."""
            self.reads += 1
            return await super().latest_instrument(product_id)

    observations = Counting(())
    store = InMemoryExecutionStore()
    snapshot = await _book(store, _strategy())
    bindings = InMemoryFuturesContractStore()
    await bindings.bind_contract(_binding(snapshot))
    with (
        cycle_reads_scope(CycleReads()),
        futures_runtime_scope(FuturesRuntime(bindings, observations)),
    ):
        await prepare_futures_book(
            snapshot,
            strategy=_strategy(),
            store=store,
            market_data=MarketDataService(DemoMarketData()),
        )
        assert await CatalogContractSizes(observations).contract_size(_PERP) == Decimal("0.01")
    assert observations.reads == 1


@pytest.mark.anyio
async def test_missing_position_list_is_unknown_never_flat() -> None:
    """A successful HTTP body without positions does not prove inventory absence."""
    source = RecordedFuturesSource(
        CoinbaseCfmAccount(_transport(positions=dict)),
        NoExternalOrders(),
        _Observations(()),
        mirror=None,
    )
    account = await source.read_account(frozenset(), frozenset({_PERP}))
    assert account.venue.positions is None


@pytest.mark.anyio
async def test_a_mirror_row_refreshed_after_the_balance_read_keeps_its_killswitch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The killswitch row is judged after its own read, never against the balance receipt.

    A mirror refresh that lands between the balance read and the mirror read used to look
    future-dated and erase a fresh, disabled killswitch, so the fleet pass blocked live entries.
    """
    balance_at = datetime(2026, 10, 28, 12, tzinfo=UTC)
    clock = iter((balance_at, balance_at + timedelta(seconds=3)))
    monkeypatch.setattr(futures_sources, "utc_now", lambda: next(clock))

    @dataclass(frozen=True)
    class _Row:
        observed_at: datetime
        margin_window: FuturesMarginWindow

    class _Mirror:
        async def latest(self) -> _Row:
            return _Row(
                balance_at + timedelta(seconds=2),
                FuturesMarginWindow("MARGIN_WINDOW_TYPE_UNSPECIFIED", None, False, False),
            )

    transport = _transport(positions=lambda: {"positions": []})
    source = RecordedFuturesSource(
        CoinbaseCfmAccount(transport),
        NoExternalOrders(),
        _Observations(()),
        mirror=cast("FuturesAccountSnapshotStore", _Mirror()),
    )
    with cycle_reads_scope(CycleReads()):
        account = await source.read_account(frozenset(), frozenset({_PERP}))
    assert account.venue.observed_at == balance_at
    assert account.venue.killswitch_enabled is False
    assert account.venue.killswitch_observed_at == balance_at + timedelta(seconds=2)
