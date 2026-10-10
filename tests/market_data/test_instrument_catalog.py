"""Instrument catalog (ADR 0126): spot stays byte-identical, futures are read-only facts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.instrument_catalog import futures_catalog_fingerprint
from thytrader.market_data.instrument_ids import (
    futures_contract_code,
    futures_listed_expiry,
    is_futures_product_id,
    normalize_market_product_id,
)
from thytrader.market_data.instruments import FuturesProduct, InstrumentKind
from thytrader.market_data.products import is_spot_product_id, normalize_spot_product_id
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.models import Instrument as StrategyInstrument

if TYPE_CHECKING:
    from thytrader.market_data.models import MarketProduct

# Pinned spot catalog identity of the demo provider. It must never move because futures
# exist; a change here means a spot fingerprint stopped being byte-identical.
_DEMO_SPOT_CATALOG_FINGERPRINT = (
    "sha256:0a019691db70ccdfd708c7133ff32dac3ea21d1711e105e515f0b4ce11544bdb"
)
_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"


def _futures() -> tuple[FuturesProduct, ...]:
    """Parse the verbatim fixture's FCM rows into domain products."""
    payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
    parsed = (parse_futures_row(row) for row in payload["products"])
    return tuple(product for product in parsed if product is not None)


class _Futures:
    """A futures provider double over the fixture, with a switchable disabled contract."""

    def __init__(self, disabled: str | None = None) -> None:
        """Optionally mark one contract venue-disabled."""
        self.disabled = disabled
        self.calls = 0

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """Return the fixture listing."""
        self.calls += 1
        return tuple(
            replace(p, trading_enabled=False) if p.product_id == self.disabled else p
            for p in _futures()
        )


def test_spot_catalog_fingerprint_is_byte_identical_with_or_without_futures() -> None:
    """Adding a futures provider changes neither the spot fingerprint nor spot products."""

    async def exercise() -> None:
        """Read the spot catalog through both service shapes."""
        plain = MarketDataService(DemoMarketData())
        with_futures = MarketDataService(DemoMarketData(), futures_provider=_Futures())
        plain_snapshot = await plain.catalog_snapshot()
        futures_snapshot = await with_futures.catalog_snapshot()
        assert plain_snapshot.fingerprint == _DEMO_SPOT_CATALOG_FINGERPRINT
        assert futures_snapshot.fingerprint == _DEMO_SPOT_CATALOG_FINGERPRINT
        assert await plain.list_enabled_spot_products() == (
            await with_futures.list_enabled_spot_products()
        )
        spot_ids = {p.product_id for p in await with_futures.list_enabled_spot_products()}
        assert not any(is_futures_product_id(product_id) for product_id in spot_ids)
        catalog = await with_futures.instrument_catalog()
        assert catalog.spot_fingerprint == _DEMO_SPOT_CATALOG_FINGERPRINT
        assert catalog.futures_fingerprint == futures_catalog_fingerprint(_futures())

    asyncio.run(exercise())


def test_instrument_catalog_lists_both_kinds_with_separate_identities() -> None:
    """Spot rows are SPOT; futures keep their own kind and never carry a spot product."""

    async def exercise() -> None:
        """Build the combined catalog from the demo spot list and the fixture."""
        service = MarketDataService(DemoMarketData(), futures_provider=_Futures())
        catalog = await service.instrument_catalog()
        kinds = {item.product_id: item.kind for item in catalog.instruments}
        assert kinds["BIP-20DEC30-CDE"] is InstrumentKind.PERPETUAL_FUTURE
        assert kinds["BIT-30OCT26-CDE"] is InstrumentKind.DATED_FUTURE
        spot_rows = [item for item in catalog.instruments if item.kind is InstrumentKind.SPOT]
        assert spot_rows
        assert all(item.spot is not None and item.future is None for item in spot_rows)

    asyncio.run(exercise())


def test_demo_mode_catalog_is_spot_only_and_futures_are_unknown() -> None:
    """Without a futures provider a futures id is never reported as enabled."""

    async def exercise() -> None:
        """Query a futures id against a spot-only service."""
        service = MarketDataService(DemoMarketData())
        catalog = await service.instrument_catalog()
        assert catalog.futures_fingerprint is None
        assert await service.enabled_instrument("BIP-20DEC30-CDE") is None

    asyncio.run(exercise())


def test_enabled_instrument_resolves_spot_and_futures_and_refuses_disabled() -> None:
    """Spot takes the unchanged spot path; a disabled contract is not enabled."""

    async def exercise() -> None:
        """Resolve one of each kind plus a disabled and an unknown contract."""
        futures = _Futures(disabled="ETP-20DEC30-CDE")
        service = MarketDataService(DemoMarketData(), futures_provider=futures)
        spot: MarketProduct = (await service.list_enabled_spot_products())[0]
        found_spot = await service.enabled_instrument(spot.product_id)
        assert found_spot is not None
        assert found_spot.spot == spot
        found_future = await service.enabled_instrument("BIP-20DEC30-CDE")
        assert found_future is not None
        assert found_future.future is not None
        assert found_future.future.underlying == "BTC"
        assert await service.enabled_instrument("ETP-20DEC30-CDE") is None
        assert await service.enabled_instrument("ZZZ-20DEC30-CDE") is None
        assert futures.calls == 1

    asyncio.run(exercise())


def test_futures_fingerprint_depends_on_exact_content() -> None:
    """A changed funding rate is a new futures observation; ordering is irrelevant."""
    products = _futures()
    assert futures_catalog_fingerprint(products) == futures_catalog_fingerprint(products[::-1])
    changed = (replace(products[0], price_increment=products[0].price_increment * 2), *products[1:])
    assert futures_catalog_fingerprint(changed) != futures_catalog_fingerprint(products)


@pytest.mark.parametrize("product_id", [p.product_id for p in _futures()])
def test_spot_only_patterns_refuse_every_futures_id(product_id: str) -> None:
    """Execution, live, adoption and discretionary surfaces keep the spot-only pattern."""
    assert not is_spot_product_id(product_id)
    with pytest.raises(ValueError, match="spot identifier"):
        normalize_spot_product_id(product_id)
    with pytest.raises(ValueError, match="product_id"):
        StrategyInstrument(product_id=product_id, base_currency="BTC", quote_currency="USD")
    assert normalize_market_product_id(product_id.lower()) == product_id


def test_futures_id_helpers() -> None:
    """The id encodes a code and a listed day, never the underlying."""
    assert is_futures_product_id("bip-20dec30-cde")
    assert not is_futures_product_id("BTC-USD")
    assert not is_futures_product_id("1000BONK-PERP-INTX")
    assert futures_contract_code("SLP-20DEC30-CDE") == "SLP"
    assert futures_listed_expiry("ET-30OCT26-CDE") == date(2026, 10, 30)
    with pytest.raises(ValueError, match="invalid expiry"):
        futures_listed_expiry("BIP-31FEB30-CDE")
    with pytest.raises(ValueError, match="futures identifier"):
        normalize_market_product_id("BTC-EUR")
