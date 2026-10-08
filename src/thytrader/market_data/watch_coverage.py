"""Pure watch-coverage arithmetic shared by ingestion, data control and operator reports.

Lookback starts, expected and covered candle counts, island coverage and listing horizons
are clock math over candles and watch settings; they hold no worker state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thytrader.market_data.freshness import FreshnessStatus, evaluate_freshness
from thytrader.market_data.hole_settlement import settle_cutoff
from thytrader.market_data.lookback import max_watch_lookback_hours
from thytrader.market_data.models import (
    HISTORICAL_REQUEST_MAX_CANDLES,
    MAX_HISTORICAL_INTERVAL_COUNT,
    CandleInterval,
)
from thytrader.market_data.worker_state import (
    MarketDataWorkerError,
)

# How far past the lookback ceiling a listing search looks: one page of daily candles.
_LISTING_SEARCH_MARGIN = timedelta(days=HISTORICAL_REQUEST_MAX_CANDLES)


def bounded_lookback_start(
    ends_at: datetime,
    lookback_hours: int,
    interval: CandleInterval,
    *,
    max_intervals: int = MAX_HISTORICAL_INTERVAL_COUNT,
) -> datetime:
    """Align a lookback window to the interval without exceeding the range cap."""
    requested = timedelta(hours=lookback_hours)
    max_span = interval.duration * max_intervals
    span = requested if requested <= max_span else max_span
    starts_at = safe_shift(
        ends_at,
        -span,
        "Market-data worker cannot represent its initial range start.",
    )
    remainder = (ends_at - starts_at) % interval.duration
    if remainder != timedelta(0):
        starts_at = safe_shift(
            starts_at,
            remainder,
            "Market-data worker cannot represent its aligned range start.",
        )
    if starts_at >= ends_at:
        raise MarketDataWorkerError("Market-data worker lookback collapsed to an empty range.")
    return starts_at


def watch_lookback_start(
    closed_end: datetime,
    lookback_hours: int,
    interval: CandleInterval,
    covered_starts_at: datetime | None,
    history_floor_at: datetime | None,
) -> datetime:
    """Return the oldest bar the watch still needs, clamped to a confirmed provider floor."""
    lookback_start = bounded_lookback_start(closed_end, lookback_hours, interval)
    if (
        history_floor_at is not None
        and covered_starts_at is not None
        and history_floor_at == covered_starts_at
        and lookback_start < history_floor_at
    ):
        return history_floor_at
    return lookback_start


def watch_expected_candle_count(
    lookback_hours: int, interval: CandleInterval, ends_at: datetime
) -> int:
    """Count bars in the watch lookback window ending at ``ends_at``."""
    start = bounded_lookback_start(ends_at, lookback_hours, interval)
    return int((ends_at - start) / interval.duration)


def watch_covered_candle_count(
    covered_starts_at: datetime | None,
    covered_ends_at: datetime | None,
    lookback_hours: int,
    interval: CandleInterval,
    ends_at: datetime,
) -> int:
    """Count the watch lookback window's bars that verified coverage spans (the X of X of Y).

    No-trade bars count as covered; bars before a listing floor do not, so a young
    market reports its real share of the lookback.
    """
    if covered_starts_at is None or covered_ends_at is None:
        return 0
    start = max(covered_starts_at, bounded_lookback_start(ends_at, lookback_hours, interval))
    end = min(covered_ends_at, ends_at)
    if end <= start:
        return 0
    return int((end - start) / interval.duration)


def island_covers_watch(
    *,
    covered_starts_at: datetime | None,
    covered_ends_at: datetime | None,
    island_complete: bool,
    lookback_hours: int,
    interval: CandleInterval,
    closed_end: datetime,
    product_id: str = "",
    now: datetime | None = None,
    history_floor_at: datetime | None = None,
) -> bool:
    """True when the latest complete island spans the full watch lookback.

    When candle freshness is still ``fresh`` and coverage is only one closed bar
    behind ``closed_end``, treat the watch as complete so large grids do not flip
    on every boundary while the single worker is elsewhere. Coverage that reaches
    the settle cutoff also spans the watch: a missing bar inside the settle window
    may still be published, so a sparse market's quiet head waits there without
    being history the watch lacks (ADR 0095).

    A ``history_floor_at`` equal to the island start means the listing search found
    no provider candle before the island (the market had not traded yet), so the
    island satisfies the lookback from that floor.
    """
    if not island_complete or covered_starts_at is None or covered_ends_at is None:
        return False
    lookback_start = watch_lookback_start(
        closed_end, lookback_hours, interval, covered_starts_at, history_floor_at
    )
    if covered_starts_at > lookback_start:
        return False
    if covered_ends_at >= closed_end or covered_ends_at >= settle_cutoff(closed_end, interval):
        return True
    if now is not None and product_id:
        freshness = evaluate_freshness(
            product_id=product_id,
            newest_candle_at=covered_ends_at,
            now=now,
            interval=interval,
        )
        if (
            freshness.status is FreshnessStatus.FRESH
            and covered_ends_at + interval.duration >= closed_end
        ):
            return True
    return False


def listing_horizon_start(closed_end: datetime, interval: CandleInterval) -> datetime:
    """Return the oldest instant a listing search covers: one daily page past the ceiling.

    The ceiling is the longest lookback a watch on the timeframe may request (ADR 0085), so
    a floor proven back to here holds for every lookback and raising one never needs it
    proven again. The extra 350 days let a search below a quiet lookback start find the
    trade that prices it, so a quiet first bar is never mistaken for a listing.
    """
    ceiling = bounded_lookback_start(closed_end, max_watch_lookback_hours(interval), interval)
    return utc_day_floor(ceiling) - _LISTING_SEARCH_MARGIN


def utc_day_floor(value: datetime) -> datetime:
    """Return the UTC midnight at or before one aware UTC instant."""
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def safe_shift(value: datetime, delta: timedelta, message: str) -> datetime:
    """Shift one worker instant without leaking an unrepresentable datetime boundary."""
    try:
        return value + delta
    except OverflowError as error:
        raise MarketDataWorkerError(message) from error
