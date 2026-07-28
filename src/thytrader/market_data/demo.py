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

_DEMO_PRODUCTS = (
    MarketProduct(
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
    MarketProduct(
        product_id="ETH-USD",
        base_currency="ETH",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    ),
    MarketProduct(
        product_id="SOL-USD",
        base_currency="SOL",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.0001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.01"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    ),
)


class DemoMarketData:
    """Provide complete synthetic USD spot previews without network access."""

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return deterministic USD spot products available to a clean local install."""
        return _DEMO_PRODUCTS

    async def get_recent_preview(
        self,
        product_id: str,
        interval: CandleInterval,
        now: datetime,
    ) -> MarketDataPreview:
        """Return deterministic completed hourly candles for one supported demo product."""
        product = next(
            (candidate for candidate in _DEMO_PRODUCTS if candidate.product_id == product_id),
            None,
        )
        if product is None or interval is not CandleInterval.ONE_HOUR:
            message = "Demo market data only supports catalog USD products on the 1h timeframe."
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
            product=product,
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
