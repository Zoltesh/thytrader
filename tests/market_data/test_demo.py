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


def test_demo_fifteen_minute_range_is_complete() -> None:
    """Demo 15m ranges are complete synthetic bars, never interpolated."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 1, 1, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.FIFTEEN_MINUTES,
            starts_at,
            ends_at,
            datetime(2026, 8, 1, 2, tzinfo=UTC),
        )
    )
    assert report.complete is True
    assert report.requested_candle_count == 4
    assert report.quality.gap_count == 0


def test_demo_thirty_minute_range_is_complete() -> None:
    """Demo 30m ranges are complete synthetic bars, never interpolated."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 1, 1, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.THIRTY_MINUTES,
            starts_at,
            ends_at,
            datetime(2026, 8, 1, 2, tzinfo=UTC),
        )
    )
    assert report.complete is True
    assert report.requested_candle_count == 2
    assert report.quality.gap_count == 0


def test_demo_six_hour_range_is_complete() -> None:
    """Demo 6h ranges are complete synthetic bars, never interpolated."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 2, 0, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.SIX_HOURS,
            starts_at,
            ends_at,
            datetime(2026, 8, 2, 6, tzinfo=UTC),
        )
    )
    assert report.complete is True
    assert report.requested_candle_count == 4
    assert report.quality.gap_count == 0
    assert tuple(candle.starts_at.hour for candle in report.quality.candles) == (0, 6, 12, 18)


def test_demo_six_hour_excludes_the_open_bar() -> None:
    """An unfinished 6h bar is excluded; the UTC day is then incomplete."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 2, 0, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.SIX_HOURS,
            starts_at,
            ends_at,
            datetime(2026, 8, 1, 20, tzinfo=UTC),
        )
    )
    assert report.complete is False
    assert report.requested_candle_count == 4
    assert report.quality.candle_count == 3
    assert tuple(candle.starts_at.hour for candle in report.quality.candles) == (0, 6, 12)


def test_demo_one_day_range_is_complete() -> None:
    """Demo 1d ranges are one complete synthetic UTC day, never interpolated."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 2, 0, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.ONE_DAY,
            starts_at,
            ends_at,
            datetime(2026, 8, 2, 6, tzinfo=UTC),
        )
    )
    assert report.complete is True
    assert report.requested_candle_count == 1
    assert report.quality.gap_count == 0
    assert report.quality.candles[0].starts_at == starts_at


def test_demo_one_day_excludes_the_open_bar() -> None:
    """An unfinished 1d bar is excluded; the UTC day is then incomplete."""
    starts_at = datetime(2026, 8, 1, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 2, 0, tzinfo=UTC)
    report = asyncio.run(
        DemoMarketData().get_historical_range(
            "ETH-USD",
            CandleInterval.ONE_DAY,
            starts_at,
            ends_at,
            datetime(2026, 8, 1, 20, tzinfo=UTC),
        )
    )
    assert report.complete is False
    assert report.requested_candle_count == 1
    assert report.quality.candle_count == 0


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
