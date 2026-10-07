"""Lookback windows and the per-timeframe research ceilings (ADR 0085)."""

from datetime import UTC, datetime, timedelta

import pytest

from thytrader.market_data.lookback import (
    MAX_WATCH_LOOKBACK_HOURS,
    WATCH_LOOKBACK_MAX_HOURS,
    describe_lookback_hours,
    max_watch_lookback_hours,
    validate_watch_lookback_hours,
)
from thytrader.market_data.models import MAX_HISTORICAL_INTERVAL_COUNT, CandleInterval
from thytrader.market_data.watch_coverage import bounded_lookback_start

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


def test_thirty_minute_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 30m window must request 4,320 bars, not an interval-cap clip."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.THIRTY_MINUTES)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.THIRTY_MINUTES.duration == (
        _NINETY_DAY_HOURS * 2
    )


def test_six_hour_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 6h window must request 360 bars, not an interval-cap clip."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.SIX_HOURS)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.SIX_HOURS.duration == (_NINETY_DAY_HOURS // 6)


def test_one_day_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 1d window must request 90 bars, not an interval-cap clip."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.ONE_DAY)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.ONE_DAY.duration == (_NINETY_DAY_HOURS // 24)


def test_hourly_ninety_day_lookback_stays_lookback_limited() -> None:
    """Raising the interval cap must not expand a 2,160-hour 1h watch to 129,600 hours."""
    ends_at = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.ONE_HOUR)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.ONE_HOUR.duration == _NINETY_DAY_HOURS


def test_one_minute_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 1m window must request 129,600 bars, not an 18-day clip."""
    ends_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.ONE_MINUTE)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.ONE_MINUTE.duration == (_NINETY_DAY_HOURS * 60)


def test_two_hour_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 2h window must request 1,080 bars, not an interval-cap clip."""
    ends_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.TWO_HOURS)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.TWO_HOURS.duration == (_NINETY_DAY_HOURS // 2)


def test_four_hour_ninety_day_lookback_is_not_clipped() -> None:
    """A 2,160-hour 4h window must request 540 bars, not an interval-cap clip."""
    ends_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    starts_at = bounded_lookback_start(ends_at, _NINETY_DAY_HOURS, CandleInterval.FOUR_HOURS)

    assert starts_at == ends_at - timedelta(hours=_NINETY_DAY_HOURS)
    assert (ends_at - starts_at) // CandleInterval.FOUR_HOURS.duration == (_NINETY_DAY_HOURS // 4)


_EXPECTED_CEILINGS = {
    CandleInterval.ONE_MINUTE: 2_160,
    CandleInterval.FIVE_MINUTES: 8_760,
    CandleInterval.FIFTEEN_MINUTES: 17_520,
    CandleInterval.THIRTY_MINUTES: 26_280,
    CandleInterval.ONE_HOUR: 43_800,
    CandleInterval.TWO_HOURS: 87_600,
    CandleInterval.FOUR_HOURS: 87_600,
    CandleInterval.SIX_HOURS: 87_600,
    CandleInterval.ONE_DAY: 87_600,
}


def test_research_lookback_ceilings_per_timeframe() -> None:
    """ADR 0085: 1m 90 d, 5m 1 y, 15m 2 y, 30m 3 y, 1h 5 y, 2h-1d 10 y."""
    assert dict(WATCH_LOOKBACK_MAX_HOURS) == _EXPECTED_CEILINGS
    assert {interval: max_watch_lookback_hours(interval) for interval in CandleInterval} == (
        _EXPECTED_CEILINGS
    )
    assert MAX_WATCH_LOOKBACK_HOURS == 87_600


@pytest.mark.parametrize("interval", list(CandleInterval))
def test_every_ceiling_fits_the_historical_interval_cap(interval: CandleInterval) -> None:
    """A full-ceiling watch never asks for more bars than one bounded range may hold."""
    ceiling = timedelta(hours=max_watch_lookback_hours(interval))
    assert ceiling // interval.duration <= MAX_HISTORICAL_INTERVAL_COUNT
    ends_at = datetime(2026, 10, 1, tzinfo=UTC)
    starts_at = bounded_lookback_start(ends_at, max_watch_lookback_hours(interval), interval)
    assert ends_at - starts_at == ceiling, "the interval cap must not clip a legal watch"


def test_lookback_validation_names_the_ceiling_and_its_span() -> None:
    """Rejections state the numeric ceiling and a readable span; the ceiling itself is legal."""
    validate_watch_lookback_hours(CandleInterval.ONE_HOUR, 43_800)
    validate_watch_lookback_hours(CandleInterval.ONE_DAY, 87_600)
    with pytest.raises(ValueError, match=r"between 1 and 43800 \(5 years\) for 1h"):
        validate_watch_lookback_hours(CandleInterval.ONE_HOUR, 43_801)
    with pytest.raises(ValueError, match=r"between 1 and 2160 \(90 days\) for 1m"):
        validate_watch_lookback_hours(CandleInterval.ONE_MINUTE, 2_161)
    with pytest.raises(ValueError, match=r"between 1 and 87600 \(10 years\) for 1d"):
        validate_watch_lookback_hours(CandleInterval.ONE_DAY, 0)
    assert describe_lookback_hours(8_760) == "1 year"
    assert describe_lookback_hours(48) == "2 days"
    assert describe_lookback_hours(5) == "5 hours"
