"""Behavioral tests for historical-candle quality analysis."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import CandleQualityError, analyze_candles, analyze_range


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


def test_quality_rejects_nonfinite_and_semantically_invalid_ohlcv_values() -> None:
    """Provider-neutral quality must reject unsafe exact OHLCV values before persistence."""
    base = _candle(0)
    invalid_candles = (
        replace(base, high=Decimal("Infinity")),
        replace(base, volume=Decimal("NaN")),
        replace(base, open=Decimal("0")),
        replace(base, low=Decimal("-1")),
        replace(base, high=Decimal("100")),
        replace(base, low=Decimal("106")),
        replace(base, volume=Decimal("-0.1")),
    )

    for candle in invalid_candles:
        with pytest.raises(ValueError, match="OHLCV"):
            analyze_candles(
                (candle,),
                CandleInterval.ONE_HOUR,
                now=datetime(2026, 7, 28, 2, 0, tzinfo=UTC),
            )


def test_quality_range_rejects_naive_candle_timestamp_with_controlled_error() -> None:
    """Range analysis must validate candle timestamps before aware-boundary comparisons."""
    naive = replace(_candle(0), starts_at=_candle(0).starts_at.replace(tzinfo=None))

    with pytest.raises(CandleQualityError, match="timezone-aware UTC"):
        analyze_range(
            (naive,),
            CandleInterval.ONE_HOUR,
            starts_at=datetime(2026, 7, 28, 0, tzinfo=UTC),
            ends_at=datetime(2026, 7, 28, 1, tzinfo=UTC),
            now=datetime(2026, 7, 28, 2, tzinfo=UTC),
        )


def test_quality_rejects_unrepresentable_completion_boundary_with_controlled_error() -> None:
    """Quality analysis must classify a max-date candle completion overflow."""
    extreme = replace(
        _candle(0),
        starts_at=datetime(9999, 12, 31, 23, tzinfo=UTC),
    )

    with pytest.raises(CandleQualityError, match="completion boundary"):
        analyze_candles(
            (extreme,),
            CandleInterval.ONE_HOUR,
            now=datetime.max.replace(tzinfo=UTC),
        )
