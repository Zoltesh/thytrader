"""Per-timeframe watch lookback ceilings shared by ingest, catalog, and agents.

The ceilings are research policy (ADR 0085), each bounded by
``MAX_HISTORICAL_INTERVAL_COUNT`` bars. Coinbase can hold less history than a
ceiling allows; the worker then records ``history_floor_at`` instead of
interpolating, so a long ceiling never invents candles.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from thytrader.market_data.models import CandleInterval

if TYPE_CHECKING:
    from collections.abc import Mapping

_HOURS_PER_DAY = 24
# Calendar years are 365 days here; leap days are not modeled by watch lookbacks.
_HOURS_PER_YEAR = 365 * _HOURS_PER_DAY

WATCH_LOOKBACK_MAX_HOURS: Mapping[CandleInterval, int] = MappingProxyType(
    {
        # 90 days: 129,600 one-minute bars, exactly the historical interval cap.
        CandleInterval.ONE_MINUTE: 90 * _HOURS_PER_DAY,
        # One year: 105,120 bars.
        CandleInterval.FIVE_MINUTES: _HOURS_PER_YEAR,
        # Two years: 70,080 bars.
        CandleInterval.FIFTEEN_MINUTES: 2 * _HOURS_PER_YEAR,
        # Three years: 52,560 bars.
        CandleInterval.THIRTY_MINUTES: 3 * _HOURS_PER_YEAR,
        # Five years: 43,800 bars.
        CandleInterval.ONE_HOUR: 5 * _HOURS_PER_YEAR,
        # Ten years: 43,800, 21,900, 14,600, and 3,650 bars.
        CandleInterval.TWO_HOURS: 10 * _HOURS_PER_YEAR,
        CandleInterval.FOUR_HOURS: 10 * _HOURS_PER_YEAR,
        CandleInterval.SIX_HOURS: 10 * _HOURS_PER_YEAR,
        CandleInterval.ONE_DAY: 10 * _HOURS_PER_YEAR,
    }
)
# The widest per-interval ceiling; PostgreSQL's watchlist CHECK constraint uses it.
MAX_WATCH_LOOKBACK_HOURS = max(WATCH_LOOKBACK_MAX_HOURS.values())


def max_watch_lookback_hours(interval: CandleInterval) -> int:
    """Return the maximum configured watch lookback for one interval."""
    return WATCH_LOOKBACK_MAX_HOURS[interval]


def describe_lookback_hours(hours: int) -> str:
    """Spell one lookback in whole years or days when it divides evenly, for messages."""
    if hours % _HOURS_PER_YEAR == 0:
        years = hours // _HOURS_PER_YEAR
        return f"{years} year" if years == 1 else f"{years} years"
    if hours % _HOURS_PER_DAY == 0:
        days = hours // _HOURS_PER_DAY
        return f"{days} day" if days == 1 else f"{days} days"
    return f"{hours} hours"


def describe_watch_lookback_ceilings() -> str:
    """Return every per-timeframe ceiling, grouping clocks that share one, for help text."""
    groups: dict[int, list[str]] = {}
    for interval in sorted(WATCH_LOOKBACK_MAX_HOURS, key=lambda item: item.duration):
        groups.setdefault(WATCH_LOOKBACK_MAX_HOURS[interval], []).append(interval.value)
    return "; ".join(
        f"{', '.join(tokens)} {hours} ({describe_lookback_hours(hours)})"
        for hours, tokens in groups.items()
    )


def validate_watch_lookback_hours(interval: CandleInterval, lookback_hours: int) -> None:
    """Reject lookbacks above the interval-specific ceiling."""
    maximum = max_watch_lookback_hours(interval)
    if lookback_hours < 1 or lookback_hours > maximum:
        message = (
            f"Watch lookback_hours must be between 1 and {maximum} "
            f"({describe_lookback_hours(maximum)}) for {interval.value}."
        )
        raise ValueError(message)
