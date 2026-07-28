"""Provider-neutral application service for read-only market-data previews."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from thytrader.market_data.models import CandleInterval, MarketDataPreview


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


class MarketDataService:
    """Coordinate a current read-only preview at a single UTC observation instant."""

    def __init__(self, provider: MarketDataProvider) -> None:
        """Initialize the service around a provider-neutral data boundary."""
        self._provider = provider

    async def get_btc_usd_hourly_preview(self) -> MarketDataPreview:
        """Return the supported initial dashboard preview for BTC-USD hourly candles."""
        return await self._provider.get_recent_preview(
            "BTC-USD",
            CandleInterval.ONE_HOUR,
            datetime.now(UTC),
        )
