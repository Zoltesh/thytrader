"""Concurrent catalog observations and authoritative omitted-product lookup."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from thytrader.market_data.demo import DemoMarketData

if TYPE_CHECKING:
    from thytrader.market_data.models import MarketProduct
from thytrader.market_data.service import MarketDataService, MarketProductNotFoundError


class _CatalogProvider(DemoMarketData):
    """Expose one listed market and authoritative rows omitted from the list."""

    def __init__(self) -> None:
        """Record provider calls and permit a controlled availability failure."""
        self.calls = 0
        self.unavailable = False

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return one catalog row or a transport failure without stale fallback."""
        self.calls += 1
        await asyncio.sleep(0)
        if self.unavailable:
            raise ConnectionError("test provider unavailable")
        return (await super().list_products())[:1]

    async def get_product(self, product_id: str) -> MarketProduct:
        """Answer direct lookup independently of the incomplete catalog listing."""
        await asyncio.sleep(0)
        for product in await super().list_products():
            if product.product_id == product_id:
                return product
        raise MarketProductNotFoundError(product_id)


def test_singleflight_and_concurrent_lookup_preserve_all_observed_rows() -> None:
    """Many readers share one fetch and distinct omitted products survive concurrent merges."""

    async def exercise() -> None:
        """Drive real service locks and cache updates on one event loop."""
        provider = _CatalogProvider()
        service = MarketDataService(provider)
        observations = await asyncio.gather(*(service.catalog_snapshot() for _ in range(25)))
        assert provider.calls == 1
        assert len({row.fingerprint for row in observations}) == 1
        all_products = await DemoMarketData().list_products()
        found = await asyncio.gather(
            *(service.enabled_spot_product(product.product_id) for product in all_products)
        )
        assert all(product is not None for product in found)
        merged = await service.catalog_snapshot()
        assert {p.product_id for p in merged.products} == {p.product_id for p in all_products}
        assert merged.observed_at == observations[0].observed_at
        assert provider.calls == 1
        assert await service.enabled_spot_product("MISSING-USD") is None
        service._catalog_expires_at = 0
        provider.unavailable = True
        with pytest.raises(ConnectionError):
            await service.catalog_snapshot()

    asyncio.run(exercise())


def test_explicit_disabled_row_cannot_be_resurrected_by_lookup() -> None:
    """An authoritative disabled market in the cached listing wins over any lookup."""

    async def exercise() -> None:
        """Seed a disabled row and check the service keeps its trading gate closed."""
        provider = _CatalogProvider()
        service = MarketDataService(provider)
        row = (await provider.list_products())[0]
        service._set_catalog((replace(row, trading_enabled=False),))
        assert await service.enabled_spot_product(row.product_id) is None

    asyncio.run(exercise())
