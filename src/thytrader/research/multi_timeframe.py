"""Closed-candle multi-timeframe alignment for HTF filters and per-indicator clocks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.market_data.models import CandleInterval, parse_candle_interval
from thytrader.strategies.models import StrategyDefinition, timeframe_seconds

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import HigherTimeframeFilter


def last_completed_bar_start(close_at: datetime, interval: CandleInterval) -> datetime:
    """Return the start of the latest HTF bar whose close is at or before ``close_at``.

    A bar covering ``[start, start + duration)`` is completed at ``start + duration``.
    Partial in-progress HTF bars are never returned.
    """
    exclusive_end = interval.align_closed_end(close_at)
    return exclusive_end - interval.duration


def interval_open_ceil(instant: datetime, timeframe: str) -> datetime:
    """Return ``instant`` when it is a bar open, otherwise the next open."""
    interval = parse_candle_interval(timeframe)
    exclusive_end = interval.align_closed_end(instant)
    if exclusive_end == instant:
        return instant
    return exclusive_end + interval.duration


def interval_open_floor_before(limit: datetime, timeframe: str) -> datetime:
    """Return the latest bar open strictly earlier than ``limit``."""
    interval = parse_candle_interval(timeframe)
    exclusive_end = interval.align_closed_end(limit)
    if exclusive_end == limit:
        return limit - interval.duration
    return exclusive_end


def earliest_evaluation_start_for_closed_bar(
    *,
    dataset_starts_at: datetime,
    timeframe: str,
    warmup_bars: int,
    decision_timeframe: str,
) -> datetime:
    """Return the earliest decision-clock ``evaluation_start`` one closed-bar dataset covers.

    Last-completed mapping plus ``warmup_bars`` requires the first evaluation instant to sit
    at least ``warmup_bars`` extra-clock bars after the dataset's first complete open.
    """
    clock = parse_candle_interval(timeframe)
    raw_start = dataset_starts_at + clock.duration * warmup_bars
    return interval_open_ceil(raw_start, decision_timeframe)


def latest_evaluation_end_for_closed_bar(
    *,
    dataset_ends_at: datetime,
    timeframe: str,
    decision_timeframe: str,
) -> datetime:
    """Return the latest decision-clock ``evaluation_end`` one closed-bar dataset covers.

    ``dataset_ends_at`` is the last complete extra-clock open. The last mapped extra-clock bar
    must close at or before that open, so ``evaluation_end`` must stay strictly before the
    next extra-clock open and on the decision clock.
    """
    clock = parse_candle_interval(timeframe)
    first_invalid = dataset_ends_at + clock.duration
    return interval_open_floor_before(first_invalid, decision_timeframe)


def closed_bar_required_coverage(
    *,
    evaluation_starts_at: datetime,
    evaluation_ends_at: datetime,
    timeframe: str,
    warmup_bars: int,
) -> tuple[datetime, datetime]:
    """Return the half-open span of last-completed bars for one extra clock.

    Coverage starts early enough for indicator warmup and the previous-LTF mapped bar
    used by crossovers on the first evaluation candle. It ends at the exclusive close
    of the last completed bar at evaluation end. Extra-TF datasets do not need a
    next-open fill candle.
    """
    interval = parse_candle_interval(timeframe)
    first_mapped_start = last_completed_bar_start(evaluation_starts_at, interval)
    last_mapped_start = last_completed_bar_start(evaluation_ends_at, interval)
    warmup_offset = interval.duration * (warmup_bars - 1)
    coverage_start = first_mapped_start - warmup_offset
    coverage_end = last_mapped_start + interval.duration
    return coverage_start, coverage_end


def closed_bar_starts(
    *,
    evaluation_starts_at: datetime,
    evaluation_ends_at: datetime,
    timeframe: str,
    warmup_bars: int,
) -> tuple[datetime, ...]:
    """Return every required extra-clock bar start for one evaluation window, oldest first."""
    coverage_start, coverage_end = closed_bar_required_coverage(
        evaluation_starts_at=evaluation_starts_at,
        evaluation_ends_at=evaluation_ends_at,
        timeframe=timeframe,
        warmup_bars=warmup_bars,
    )
    interval = parse_candle_interval(timeframe)
    starts: list[datetime] = []
    cursor = coverage_start
    while cursor < coverage_end:
        starts.append(cursor)
        cursor = cursor + interval.duration
    return tuple(starts)


def htf_required_coverage(
    *,
    evaluation_starts_at: datetime,
    evaluation_ends_at: datetime,
    htf_filter: HigherTimeframeFilter,
) -> tuple[datetime, datetime]:
    """Return the half-open HTF span required for one LTF evaluation window."""
    return closed_bar_required_coverage(
        evaluation_starts_at=evaluation_starts_at,
        evaluation_ends_at=evaluation_ends_at,
        timeframe=htf_filter.timeframe,
        warmup_bars=htf_filter.data_requirements.warmup_bars,
    )


def htf_candle_starts(
    *,
    evaluation_starts_at: datetime,
    evaluation_ends_at: datetime,
    htf_filter: HigherTimeframeFilter,
) -> tuple[datetime, ...]:
    """Return every required HTF bar start for one evaluation window, oldest first."""
    return closed_bar_starts(
        evaluation_starts_at=evaluation_starts_at,
        evaluation_ends_at=evaluation_ends_at,
        timeframe=htf_filter.timeframe,
        warmup_bars=htf_filter.data_requirements.warmup_bars,
    )


def mapped_htf_start(ltf_close: datetime, htf_timeframe: str) -> datetime:
    """Map one LTF close instant onto the last completed HTF bar start."""
    return last_completed_bar_start(ltf_close, parse_candle_interval(htf_timeframe))


def ltf_close(candle_starts_at: datetime, ltf_timeframe: str) -> datetime:
    """Return the exclusive close of one LTF decision bar."""
    return candle_starts_at + timedelta(seconds=timeframe_seconds(ltf_timeframe))


def strategy_requires_htf(definition: StrategyDefinition) -> bool:
    """Return whether the published strategy declares an HTF filter."""
    return definition.htf_filter is not None


def index_candles_by_start(candles: Sequence[Candle]) -> dict[datetime, Candle]:
    """Index candles by start, rejecting duplicate timestamps."""
    indexed: dict[datetime, Candle] = {}
    for candle in candles:
        if candle.starts_at in indexed:
            raise ValueError("Closed-bar candles contain duplicate timestamps.")
        indexed[candle.starts_at] = candle
    return indexed


def bars_closed_at_or_before(
    candles: Sequence[Candle],
    *,
    close_at: datetime,
    timeframe: str,
) -> tuple[Candle, ...]:
    """Return bars whose exclusive close is at or before ``close_at``.

    In-progress and later bars are dropped so LTF evaluation cannot look ahead.
    """
    duration = parse_candle_interval(timeframe).duration
    return tuple(candle for candle in candles if candle.starts_at + duration <= close_at)


def htf_bars_closed_at_or_before(
    htf_candles: Sequence[Candle],
    *,
    close_at: datetime,
    htf_timeframe: str,
) -> tuple[Candle, ...]:
    """Return HTF bars whose exclusive close is at or before ``close_at``."""
    return bars_closed_at_or_before(htf_candles, close_at=close_at, timeframe=htf_timeframe)
