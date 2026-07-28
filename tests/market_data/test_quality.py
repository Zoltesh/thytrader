"""Behavioral tests for historical-candle quality analysis."""

from datetime import UTC, datetime
from decimal import Decimal

from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_candles


def _candle(hour: int) -> Candle:
    """Build one exact, hourly candle for quality-analysis fixtures."""
    return Candle(
        starts_at=datetime(2026, 7, 28, hour, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("12.5"),
    )


def test_quality_excludes_incomplete_bars_and_counts_missing_hourly_intervals() -> None:
    """Quality must not use an unfinished bar and must expose a gap without interpolation."""
    report = analyze_candles(
        (_candle(0), _candle(1), _candle(3), _candle(5)),
        CandleInterval.ONE_HOUR,
        now=datetime(2026, 7, 28, 5, 30, tzinfo=UTC),
    )

    assert tuple(candle.starts_at.hour for candle in report.candles) == (0, 1, 3)
    assert report.candle_count == 3
    assert report.missing_intervals == 1
    assert report.gap_count == 1
    assert report.latest_completed_at == datetime(2026, 7, 28, 4, tzinfo=UTC)
    assert report.is_stale is False


def test_quality_marks_old_completed_data_stale() -> None:
    """A latest completed bar older than two expected intervals must be visibly stale."""
    report = analyze_candles(
        (_candle(0),),
        CandleInterval.ONE_HOUR,
        now=datetime(2026, 7, 28, 3, 1, tzinfo=UTC),
    )

    assert report.candle_count == 1
    assert report.missing_intervals == 0
    assert report.is_stale is True
