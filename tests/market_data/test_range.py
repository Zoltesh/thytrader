"""Behavioral tests for explicit historical-candle range completeness."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range


def _candle(hour: int) -> Candle:
    """Build one exact one-hour candle for a requested range fixture."""
    return Candle(
        starts_at=datetime(2026, 7, 1, hour, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("12.5"),
    )


def test_range_report_exposes_missing_expected_candle_as_incomplete() -> None:
    """A requested closed range must report missing coverage rather than imply completeness."""
    report = analyze_range(
        (_candle(0), _candle(2)),
        CandleInterval.ONE_HOUR,
        starts_at=datetime(2026, 7, 1, 0, tzinfo=UTC),
        ends_at=datetime(2026, 7, 1, 3, tzinfo=UTC),
        now=datetime(2026, 7, 1, 4, tzinfo=UTC),
    )

    assert report.requested_candle_count == 3
    assert report.quality.candle_count == 2
    assert report.quality.gap_count == 1
    assert report.quality.missing_intervals == 1
    assert report.complete is False


def test_range_rejects_unexpected_candle_outside_requested_window() -> None:
    """Only the exact exclusive boundary candle may be ignored; other out-of-range data fails."""
    with pytest.raises(ValueError, match="requested half-open range"):
        analyze_range(
            (_candle(0), _candle(1), _candle(3)),
            CandleInterval.ONE_HOUR,
            starts_at=datetime(2026, 7, 1, 0, tzinfo=UTC),
            ends_at=datetime(2026, 7, 1, 2, tzinfo=UTC),
            now=datetime(2026, 7, 1, 4, tzinfo=UTC),
        )
