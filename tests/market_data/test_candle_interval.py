"""Every ingested venue interval is a complete-only dataset and an execution clock."""

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


def test_fifteen_minute_is_an_execution_clock() -> None:
    """15m datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("15m")
    assert interval is CandleInterval.FIFTEEN_MINUTES
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is True
    assert as_dataset_timeframe(interval) == "15m"


def test_thirty_minute_is_an_execution_clock() -> None:
    """30m datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("30m")
    assert interval is CandleInterval.THIRTY_MINUTES
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is True
    assert as_dataset_timeframe(interval) == "30m"


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


def test_six_hour_is_an_execution_clock() -> None:
    """6h datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("6h")
    assert interval is CandleInterval.SIX_HOURS
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is False
    assert as_dataset_timeframe(interval) == "6h"


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


def test_one_day_is_an_execution_clock() -> None:
    """1d datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("1d")
    assert interval is CandleInterval.ONE_DAY
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is False
    assert as_dataset_timeframe(interval) == "1d"


def test_one_minute_duration_and_alignment() -> None:
    """1m bars are UTC-aligned at every minute."""
    interval = CandleInterval.ONE_MINUTE
    assert interval.duration == timedelta(minutes=1)
    assert interval.value == "1m"
    now = datetime(2026, 9, 15, 12, 34, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 15, 12, 34, tzinfo=UTC)
    closed = datetime(2026, 9, 15, 12, 34, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed
    before_minute = datetime(2026, 9, 15, 12, 33, 59, tzinfo=UTC)
    assert interval.align_closed_end(before_minute) == datetime(2026, 9, 15, 12, 33, tzinfo=UTC)


def test_one_minute_is_an_execution_clock() -> None:
    """1m datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("1m")
    assert interval is CandleInterval.ONE_MINUTE
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is True
    assert as_dataset_timeframe(interval) == "1m"


def test_two_hour_duration_and_alignment() -> None:
    """2h bars are UTC-aligned at even hours."""
    interval = CandleInterval.TWO_HOURS
    assert interval.duration == timedelta(hours=2)
    assert interval.value == "2h"
    now = datetime(2026, 9, 15, 13, 1, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    closed = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed
    before_noon = datetime(2026, 9, 15, 11, 59, 59, tzinfo=UTC)
    assert interval.align_closed_end(before_noon) == datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    midnight = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
    assert interval.align_closed_end(midnight) == midnight
    twenty_two = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
    assert interval.align_closed_end(twenty_two) == twenty_two


def test_two_hour_is_an_execution_clock() -> None:
    """2h datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("2h")
    assert interval is CandleInterval.TWO_HOURS
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is False
    assert as_dataset_timeframe(interval) == "2h"


def test_four_hour_duration_and_alignment() -> None:
    """4h bars are UTC-aligned at 00:00, 04:00, 08:00, 12:00, 16:00, and 20:00."""
    interval = CandleInterval.FOUR_HOURS
    assert interval.duration == timedelta(hours=4)
    assert interval.value == "4h"
    now = datetime(2026, 9, 15, 13, 1, 40, tzinfo=UTC)
    assert interval.align_closed_end(now) == datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    closed = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    assert interval.align_closed_end(closed) == closed
    before_noon = datetime(2026, 9, 15, 11, 59, 59, tzinfo=UTC)
    assert interval.align_closed_end(before_noon) == datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    midnight = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
    assert interval.align_closed_end(midnight) == midnight
    twenty = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
    assert interval.align_closed_end(twenty) == twenty
    four = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
    assert interval.align_closed_end(four) == four


def test_four_hour_is_an_execution_clock() -> None:
    """4h datasets parse and are a legal paper/live clock."""
    interval = parse_candle_interval("4h")
    assert interval is CandleInterval.FOUR_HOURS
    assert interval.execution_supported is True
    assert interval.requires_live_user_feed is False
    assert as_dataset_timeframe(interval) == "4h"
