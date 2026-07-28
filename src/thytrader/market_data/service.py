"""Provider-neutral application service for read-only market-data diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from thytrader.market_data.models import (
    CandleInterval,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)


class MarketDataProvider(Protocol):
    """Read-only preview and product-catalog capability used by dashboard presentation."""

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Return product metadata plus validated recent closed candles."""
        ...

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return the provider's current normalized spot-product catalog."""
        ...


class HistoricalMarketDataProvider(MarketDataProvider, Protocol):
    """Extended provider boundary required only by bounded range diagnostics."""

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return validated quality facts for one explicit closed-candle range."""
        ...


class MarketDataService:
    """Coordinate read-only previews and bounded diagnostics at UTC observation instants."""

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
            product_id, CandleInterval.ONE_HOUR, datetime.now(UTC)
        )

    async def get_recent_hourly_range(self, product_id: str) -> CandleRangeReport:
        """Return a seven-day closed hourly range for dashboard completeness diagnostics."""
        ends_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        provider = cast("HistoricalMarketDataProvider", self._provider)
        return await provider.get_historical_range(
            product_id,
            CandleInterval.ONE_HOUR,
            ends_at - timedelta(days=7),
            ends_at,
            ends_at,
        )
