"""Complete-only HTF coverage checks for the execution worker."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from thytrader.execution_worker.service import htf_coverage_ready
from thytrader.market_data.models import Candle

_HOUR = timedelta(hours=1)


def _bar(hour: int) -> Candle:
    """Return a placeholder hourly candle starting at the given hour."""
    start = datetime(2026, 1, 1, hour, tzinfo=UTC)
    price = Decimal("100") + Decimal(hour)
    return Candle(
        starts_at=start,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("10"),
    )


def test_htf_coverage_ready_requires_latest_completed_bar() -> None:
    """A missing latest HTF bar is not ready for paper/live evaluation."""
    candles = tuple(_bar(hour) for hour in range(3))
    assert (
        htf_coverage_ready(
            candles,
            expected_last_start=datetime(2026, 1, 1, 2, tzinfo=UTC),
            bar_duration=_HOUR,
        )
        is True
    )
    assert (
        htf_coverage_ready(
            candles,
            expected_last_start=datetime(2026, 1, 1, 3, tzinfo=UTC),
            bar_duration=_HOUR,
        )
        is False
    )


def test_htf_coverage_ready_rejects_gaps() -> None:
    """HTF holes cannot be interpolated into a complete window."""
    candles = (_bar(0), _bar(1), _bar(3))
    assert (
        htf_coverage_ready(
            candles,
            expected_last_start=datetime(2026, 1, 1, 3, tzinfo=UTC),
            bar_duration=_HOUR,
        )
        is False
    )
