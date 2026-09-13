"""Fifteen-minute dataset intervals stay complete-only and off the execution clocks."""

from datetime import UTC, datetime, timedelta

from thytrader.market_data.models import CandleInterval, as_dataset_timeframe, parse_candle_interval


def test_fifteen_minute_duration_and_alignment() -> None:
    """15m bars are UTC-aligned at :00, :15, :30, and :45."""
    interval = CandleInterval.FIFTEEN_MINUTES
    assert interval.duration == timedelta(minutes=15)
    assert interval.value == "15m"
    now = datetime(2026, 9, 13, 12, 16, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 13, 12, 15, tzinfo=UTC)
    closed = datetime(2026, 9, 13, 12, 15, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed


def test_fifteen_minute_is_a_dataset_interval_not_an_execution_clock() -> None:
    """15m datasets parse; paper/live still refuse that clock."""
    interval = parse_candle_interval("15m")
    assert interval is CandleInterval.FIFTEEN_MINUTES
    assert interval.execution_supported is False
    assert as_dataset_timeframe(interval) == "15m"
    assert CandleInterval.ONE_HOUR.execution_supported is True
    assert CandleInterval.FIVE_MINUTES.execution_supported is True
