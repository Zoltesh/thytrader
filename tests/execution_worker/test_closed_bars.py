"""Closed-bar catch-up and gap detection for the execution worker."""

from datetime import UTC, datetime
from decimal import Decimal

from thytrader.execution_worker.service import new_closed_bars
from thytrader.market_data.models import Candle


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


def test_new_closed_bars_replays_contiguous_missed_hours() -> None:
    """Downtime of several hours evaluates each missed closed bar in order."""
    candles = tuple(_bar(hour) for hour in range(4))
    due = new_closed_bars(
        candles,
        last_evaluated_bar=datetime(2026, 1, 1, 0, tzinfo=UTC),
        expected_last_start=datetime(2026, 1, 1, 3, tzinfo=UTC),
    )
    assert due is not None
    assert [candle.starts_at.hour for candle in due] == [1, 2, 3]


def test_new_closed_bars_starts_with_only_the_latest_bar() -> None:
    """The first evaluation must not replay the warmup window as entries."""
    candles = tuple(_bar(hour) for hour in range(4))
    due = new_closed_bars(
        candles,
        last_evaluated_bar=None,
        expected_last_start=datetime(2026, 1, 1, 3, tzinfo=UTC),
    )
    assert due == (candles[-1],)


def test_new_closed_bars_returns_none_when_latest_bar_is_stale() -> None:
    """A missing latest closed hour is fail-closed."""
    candles = tuple(_bar(hour) for hour in range(3))
    due = new_closed_bars(
        candles,
        last_evaluated_bar=datetime(2026, 1, 1, 0, tzinfo=UTC),
        expected_last_start=datetime(2026, 1, 1, 4, tzinfo=UTC),
    )
    assert due is None


def test_new_closed_bars_returns_none_on_hourly_gap() -> None:
    """A hole in the series cannot be treated as consecutive indicator bars."""
    candles = (_bar(0), _bar(1), _bar(3))
    due = new_closed_bars(
        candles,
        last_evaluated_bar=datetime(2026, 1, 1, 0, tzinfo=UTC),
        expected_last_start=datetime(2026, 1, 1, 3, tzinfo=UTC),
    )
    assert due is None
