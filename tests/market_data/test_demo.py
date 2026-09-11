"""Boundary tests for deterministic demo market data."""

import asyncio
from datetime import UTC, datetime

import pytest

from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import CandleInterval


def test_demo_recent_preview_maps_minimum_datetime_overflow() -> None:
    """A minimum-date preview request must fail as a controlled value error."""
    with pytest.raises(ValueError, match="represent"):
        asyncio.run(
            DemoMarketData().get_recent_preview(
                "BTC-USD",
                CandleInterval.ONE_HOUR,
                datetime.min.replace(tzinfo=UTC),
            )
        )


def test_demo_five_minute_range_is_complete() -> None:
    """Demo 5m ranges are complete synthetic bars, never interpolated."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 1, 1, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.FIVE_MINUTES,
            starts_at,
            ends_at,
            datetime(2026, 8, 1, 2, tzinfo=UTC),
        )
    )
    assert report.complete is True
    assert report.requested_candle_count == 12
    assert report.quality.gap_count == 0


def test_demo_five_minute_range_covers_more_than_legacy_interval_cap() -> None:
    """Demo 5m history past 4,032 bars stays complete synthetic coverage, not interpolated."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    bar_count = 4_033
    ends_at = starts_at + CandleInterval.FIVE_MINUTES.duration * bar_count
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.FIVE_MINUTES,
            starts_at,
            ends_at,
            ends_at + CandleInterval.FIVE_MINUTES.duration,
        )
    )
    assert report.complete is True
    assert report.requested_candle_count == bar_count
    assert report.quality.candle_count == bar_count
    assert report.quality.gap_count == 0
    assert report.quality.candles[0].starts_at == starts_at


def test_demo_historical_range_maps_mixed_timezone_inputs() -> None:
    """A mixed-timezone historical request must not leak Python comparison errors."""
    with pytest.raises(ValueError, match="timestamp range"):
        asyncio.run(
            DemoMarketData().get_historical_range(
                "BTC-USD",
                CandleInterval.ONE_HOUR,
                datetime(2026, 8, 1, tzinfo=UTC),
                datetime.fromisoformat("2026-08-01T01:00:00"),
                datetime(2026, 8, 1, 2, tzinfo=UTC),
            )
        )
