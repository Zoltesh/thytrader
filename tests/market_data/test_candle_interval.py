"""Fifteen- and thirty-minute dataset intervals stay complete-only and off the execution clocks."""

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


def test_thirty_minute_duration_and_alignment() -> None:
    """30m bars are UTC-aligned at :00 and :30."""
    interval = CandleInterval.THIRTY_MINUTES
    assert interval.duration == timedelta(minutes=30)
    assert interval.value == "30m"
    now = datetime(2026, 9, 13, 12, 31, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 13, 12, 30, tzinfo=UTC)
    closed = datetime(2026, 9, 13, 12, 30, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed
    before_half = datetime(2026, 9, 13, 12, 29, 59, tzinfo=UTC)
    assert interval.align_closed_end(before_half) == datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def test_fifteen_minute_is_a_dataset_interval_not_an_execution_clock() -> None:
    """15m datasets parse; paper/live still refuse that clock."""
    interval = parse_candle_interval("15m")
    assert interval is CandleInterval.FIFTEEN_MINUTES
    assert interval.execution_supported is False
    assert as_dataset_timeframe(interval) == "15m"
    assert CandleInterval.ONE_HOUR.execution_supported is True
    assert CandleInterval.FIVE_MINUTES.execution_supported is True


def test_thirty_minute_is_a_dataset_interval_not_an_execution_clock() -> None:
    """30m datasets parse; paper/live still refuse that clock."""
    interval = parse_candle_interval("30m")
    assert interval is CandleInterval.THIRTY_MINUTES
    assert interval.execution_supported is False
    assert as_dataset_timeframe(interval) == "30m"
    assert CandleInterval.ONE_HOUR.execution_supported is True
    assert CandleInterval.FIVE_MINUTES.execution_supported is True


def test_six_hour_duration_and_alignment() -> None:
    """6h bars are UTC-aligned at 00:00, 06:00, 12:00, and 18:00."""
    interval = CandleInterval.SIX_HOURS
    assert interval.duration == timedelta(hours=6)
    assert interval.value == "6h"
    now = datetime(2026, 9, 13, 12, 1, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    closed = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed
    before_noon = datetime(2026, 9, 13, 11, 59, 59, tzinfo=UTC)
    assert interval.align_closed_end(before_noon) == datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    midnight = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    assert interval.align_closed_end(midnight) == midnight
    eighteen = datetime(2026, 9, 13, 18, 0, tzinfo=UTC)
    assert interval.align_closed_end(eighteen) == eighteen


def test_six_hour_is_a_dataset_interval_not_an_execution_clock() -> None:
    """6h datasets parse; paper/live still refuse that clock."""
    interval = parse_candle_interval("6h")
    assert interval is CandleInterval.SIX_HOURS
    assert interval.execution_supported is False
    assert as_dataset_timeframe(interval) == "6h"
    assert CandleInterval.ONE_HOUR.execution_supported is True
    assert CandleInterval.FIVE_MINUTES.execution_supported is True


def test_one_day_duration_and_alignment() -> None:
    """1d bars are UTC-aligned at the 00:00 day boundary."""
    interval = CandleInterval.ONE_DAY
    assert interval.duration == timedelta(days=1)
    assert interval.value == "1d"
    now = datetime(2026, 9, 13, 12, 1, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    closed = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed
    before_midnight = datetime(2026, 9, 13, 23, 59, 59, tzinfo=UTC)
    assert interval.align_closed_end(before_midnight) == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    noon = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    assert interval.align_closed_end(noon) == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)


def test_one_day_is_a_dataset_interval_not_an_execution_clock() -> None:
    """1d datasets parse; paper/live still refuse that clock."""
    interval = parse_candle_interval("1d")
    assert interval is CandleInterval.ONE_DAY
    assert interval.execution_supported is False
    assert as_dataset_timeframe(interval) == "1d"
    assert CandleInterval.ONE_HOUR.execution_supported is True
    assert CandleInterval.FIVE_MINUTES.execution_supported is True
