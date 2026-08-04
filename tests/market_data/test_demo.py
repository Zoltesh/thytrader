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
