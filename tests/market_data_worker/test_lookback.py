"""Lookback windows must cover 90-day 5m research without inflating 1h history."""

from datetime import UTC, datetime, timedelta

from thytrader.market_data.models import CandleInterval
from thytrader.market_data_worker.service import bounded_lookback_start

_NINETY_DAY_HOURS = 2_160
_FIVE_MINUTE_BARS_PER_HOUR = 12


def test_five_minute_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 5m window must request 25,920 bars, not a 14-day clip."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.FIVE_MINUTES)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.FIVE_MINUTES.duration == (
        _NINETY_DAY_HOURS * _FIVE_MINUTE_BARS_PER_HOUR
    )


def test_fifteen_minute_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 15m window must request 8,640 bars, not an interval-cap clip."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.FIFTEEN_MINUTES)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.FIFTEEN_MINUTES.duration == (
        _NINETY_DAY_HOURS * 4
    )


def test_hourly_ninety_day_lookback_stays_lookback_limited() -> None:
    """Raising the interval cap must not expand a 2,160-hour 1h watch to 25,920 hours."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.ONE_HOUR)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.ONE_HOUR.duration == _NINETY_DAY_HOURS
