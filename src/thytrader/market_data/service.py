"""Provider-neutral application service for read-only market-data previews."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from thytrader.market_data.models import CandleInterval, MarketDataPreview, MarketProduct


class MarketDataProvider(Protocol):
    """Read-only market-data capability used by dashboard presentation."""

    async def get_recent_preview(
        self,
        product_id: str,
        interval: CandleInterval,
        now: datetime,
    ) -> MarketDataPreview:
        """Return product metadata plus validated recent closed candles."""
        ...

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return the provider's current normalized spot-product catalog."""
        ...


class MarketDataService:
    """Coordinate a current read-only preview at a single UTC observation instant."""

    def __init__(self, provider: MarketDataProvider) -> None:
        """Initialize the service around a provider-neutral data boundary."""
        self._provider = provider

    async def list_enabled_usd_spot_products(self) -> tuple[MarketProduct, ...]:
        """Return deterministic selectable USD spot products without disabled markets."""
        products = await self._provider.list_products()
        return tuple(
            sorted(
                (
                    product
                    for product in products
                    if product.quote_currency == "USD" and product.trading_enabled
                ),
                key=lambda product: product.product_id,
            )
        )

    async def get_hourly_preview(self, product_id: str) -> MarketDataPreview:
        """Return one selected product's read-only hourly preview at a UTC instant."""
        return await self._provider.get_recent_preview(
            product_id,
            CandleInterval.ONE_HOUR,
            datetime.now(UTC),
        )
