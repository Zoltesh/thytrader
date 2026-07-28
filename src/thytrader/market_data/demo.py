"""Deterministic market-data preview for clean local installs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.quality import analyze_candles


class DemoMarketData:
    """Provide a complete synthetic BTC-USD hourly preview without network access."""

    async def get_recent_preview(
        self,
        product_id: str,
        interval: CandleInterval,
        now: datetime,
    ) -> MarketDataPreview:
        """Return deterministic, completed hourly candles for the supported dashboard selection."""
        if product_id != "BTC-USD" or interval is not CandleInterval.ONE_HOUR:
            message = "Demo market data only supports BTC-USD on the 1h timeframe."
            raise ValueError(message)
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            message = "Demo market data requires a timezone-aware UTC observation instant."
            raise ValueError(message)
        latest_start = now.replace(minute=0, second=0, microsecond=0) - interval.duration
        candles = tuple(
            _candle(latest_start - interval.duration * offset, offset)
            for offset in range(23, -1, -1)
        )
        return MarketDataPreview(
            product=MarketProduct(
                product_id="BTC-USD",
                base_currency="BTC",
                quote_currency="USD",
                price_increment=Decimal("0.01"),
                base_increment=Decimal("0.00000001"),
                quote_increment=Decimal("0.01"),
                base_min_size=Decimal("0.0001"),
                quote_min_size=Decimal("1"),
                trading_enabled=True,
            ),
            interval=interval,
            as_of=now,
            quality=analyze_candles(candles, interval, now),
        )


def _candle(starts_at: datetime, offset: int) -> Candle:
    """Create one monotonic exact hourly OHLCV demo candle."""
    open_price = Decimal("100000") + Decimal(offset * 125)
    return Candle(
        starts_at=starts_at,
        open=open_price,
        high=open_price + Decimal("200"),
        low=open_price - Decimal("150"),
        close=open_price + Decimal("75"),
        volume=Decimal("12.5"),
    )
