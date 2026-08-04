"""Deterministic market-data diagnostics for clean local installs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.quality import analyze_candles, analyze_range

_DEMO_PRODUCTS = (
    MarketProduct(
        "BTC-USD",
        "BTC",
        "USD",
        Decimal("0.01"),
        Decimal("0.00000001"),
        Decimal("0.01"),
        Decimal("0.0001"),
        Decimal("1"),
        True,
    ),
    MarketProduct(
        "ETH-USD",
        "ETH",
        "USD",
        Decimal("0.01"),
        Decimal("0.00000001"),
        Decimal("0.01"),
        Decimal("0.001"),
        Decimal("1"),
        True,
    ),
    MarketProduct(
        "SOL-USD",
        "SOL",
        "USD",
        Decimal("0.01"),
        Decimal("0.0001"),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("1"),
        True,
    ),
)


class DemoMarketData:
    """Provide complete synthetic USD spot diagnostics without network access."""

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return deterministic USD spot products available to a clean local install."""
        return _DEMO_PRODUCTS

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Return deterministic completed hourly candles for one supported demo product."""
        product = _product(product_id, interval)
        try:
            latest_start = now.replace(minute=0, second=0, microsecond=0) - interval.duration
            first_start = latest_start - interval.duration * 23
        except OverflowError as error:
            raise ValueError(
                "Demo market-data preview cannot represent its requested timestamp range."
            ) from error
        candles = tuple(
            _candle(first_start + interval.duration * offset, 23 - offset) for offset in range(24)
        )
        return MarketDataPreview(product, interval, now, analyze_candles(candles, interval, now))

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return deterministic complete candles for one bounded hourly demo range."""
        _product(product_id, interval)
        try:
            count = (ends_at - starts_at) // interval.duration
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError(
                "Demo market-data range cannot represent its requested timestamp range."
            ) from error
        candles = tuple(
            _candle(starts_at + interval.duration * offset, offset) for offset in range(count)
        )
        return analyze_range(candles, interval, starts_at, ends_at, now)


def _product(product_id: str, interval: CandleInterval) -> MarketProduct:
    """Return one supported demo product or fail closed for unsupported requests."""
    product = next(
        (candidate for candidate in _DEMO_PRODUCTS if candidate.product_id == product_id), None
    )
    if product is None or interval is not CandleInterval.ONE_HOUR:
        raise ValueError("Demo market data only supports catalog USD products on the 1h timeframe.")
    return product


def _candle(starts_at: datetime, offset: int) -> Candle:
    """Create one monotonic exact hourly OHLCV demo candle."""
    if starts_at.tzinfo is None or starts_at.utcoffset() != UTC.utcoffset(starts_at):
        raise ValueError("Demo market data requires a timezone-aware UTC observation instant.")
    open_price = Decimal("100000") + Decimal(offset * 125)
    return Candle(
        starts_at,
        open_price,
        open_price + Decimal("200"),
        open_price - Decimal("150"),
        open_price + Decimal("75"),
        Decimal("12.5"),
    )
