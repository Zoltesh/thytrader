"""Per-timeframe watch lookback ceilings shared by ingest, catalog, and agents."""

from __future__ import annotations

from thytrader.market_data.models import CandleInterval

# Ninety days for sub-daily clocks that already hit the 129,600-interval cap at 1m.
_DEFAULT_WATCH_LOOKBACK_HOURS = 2_160
# Three hundred sixty-five days for slower venue clocks used in low-trade-count research.
_EXTENDED_WATCH_LOOKBACK_HOURS = 8_760

_FAST_INTERVALS = frozenset(
    {
        CandleInterval.ONE_MINUTE,
        CandleInterval.FIVE_MINUTES,
        CandleInterval.FIFTEEN_MINUTES,
        CandleInterval.THIRTY_MINUTES,
        CandleInterval.ONE_HOUR,
    }
)


def max_watch_lookback_hours(interval: CandleInterval) -> int:
    """Return the maximum configured watch lookback for one interval."""
    if interval in _FAST_INTERVALS:
        return _DEFAULT_WATCH_LOOKBACK_HOURS
    return _EXTENDED_WATCH_LOOKBACK_HOURS


def validate_watch_lookback_hours(interval: CandleInterval, lookback_hours: int) -> None:
    """Reject lookbacks above the interval-specific ceiling."""
    maximum = max_watch_lookback_hours(interval)
    if lookback_hours < 1 or lookback_hours > maximum:
        message = f"Watch lookback_hours must be between 1 and {maximum} for {interval.value}."
        raise ValueError(message)
