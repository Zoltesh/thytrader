"""Validation and quality analysis for closed historical candles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleQualityReport,
    CandleRangeReport,
)


class CandleQualityError(ValueError):
    """Signal invalid candle values or timestamps that prevent trustworthy analysis."""


def validate_candle_values(candle: Candle) -> None:
    """Reject non-finite or semantically impossible exact OHLCV values."""
    values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise CandleQualityError("Candle OHLCV values must be finite Decimal values.")
    if (
        candle.open <= 0
        or candle.high <= 0
        or candle.low <= 0
        or candle.close <= 0
        or candle.volume < 0
        or candle.high < max(candle.open, candle.close)
        or candle.low > min(candle.open, candle.close)
        or candle.high < candle.low
    ):
        raise CandleQualityError("Candle OHLCV values are semantically invalid.")


def validate_candle_timestamp(value: datetime) -> None:
    """Reject candle timestamps that are not timezone-aware UTC instants."""
    _require_utc(value)


def analyze_candles(
    candles: tuple[Candle, ...],
    interval: CandleInterval,
    now: datetime,
) -> CandleQualityReport:
    """Keep completed UTC candles and report deterministic gaps and freshness.

    Incomplete candles are omitted because their values can still change. Duplicate,
    naive, or non-interval-aligned data is rejected rather than silently repaired.
    """
    _require_utc(now)
    ordered = tuple(sorted(candles, key=lambda candle: candle.starts_at))
    for candle in ordered:
        validate_candle_values(candle)
    _validate_timestamps(ordered, interval)
    completed = tuple(candle for candle in ordered if candle.starts_at + interval.duration <= now)
    gap_count, missing_intervals = _gaps(completed, interval)
    latest_completed_at = completed[-1].starts_at + interval.duration if completed else None
    is_stale = latest_completed_at is not None and now - latest_completed_at > interval.duration * 2
    return CandleQualityReport(
        candles=completed,
        candle_count=len(completed),
        gap_count=gap_count,
        missing_intervals=missing_intervals,
        latest_completed_at=latest_completed_at,
        is_stale=is_stale,
    )


def analyze_range(
    candles: tuple[Candle, ...],
    interval: CandleInterval,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> CandleRangeReport:
    """Analyze one explicit half-open UTC range without treating missing coverage as valid data."""
    _require_utc(starts_at)
    _require_utc(ends_at)
    if starts_at >= ends_at or (ends_at - starts_at) % interval.duration != timedelta(0):
        message = "Historical ranges must be non-empty and align to the selected interval."
        raise CandleQualityError(message)
    for candle in candles:
        validate_candle_timestamp(candle.starts_at)
    if any(candle.starts_at < starts_at or candle.starts_at >= ends_at for candle in candles):
        message = "Historical candles must fall within the requested half-open range."
        raise CandleQualityError(message)
    quality = analyze_candles(candles, interval, now)
    requested_candle_count = (ends_at - starts_at) // interval.duration
    complete = (
        quality.candle_count == requested_candle_count
        and quality.gap_count == 0
        and bool(quality.candles)
        and quality.candles[0].starts_at == starts_at
        and quality.latest_completed_at == ends_at
    )
    return CandleRangeReport(
        starts_at=starts_at,
        ends_at=ends_at,
        requested_candle_count=requested_candle_count,
        quality=quality,
        complete=complete,
    )


def _validate_timestamps(candles: tuple[Candle, ...], interval: CandleInterval) -> None:
    """Reject duplicate, naive, or non-aligned upstream candle timestamps."""
    previous: datetime | None = None
    for candle in candles:
        _require_utc(candle.starts_at)
        if previous is not None:
            elapsed = candle.starts_at - previous
            if elapsed <= timedelta(0):
                message = "Candle timestamps must be strictly increasing."
                raise CandleQualityError(message)
            if elapsed % interval.duration != timedelta(0):
                message = "Candle timestamps must align to the selected interval."
                raise CandleQualityError(message)
        previous = candle.starts_at


def _gaps(candles: tuple[Candle, ...], interval: CandleInterval) -> tuple[int, int]:
    """Count discontinuities and omitted bars between consecutive completed candles."""
    gaps = 0
    missing = 0
    for previous, current in pairwise(candles):
        elapsed = current.starts_at - previous.starts_at
        intervals = elapsed // interval.duration
        if intervals > 1:
            gaps += 1
            missing += intervals - 1
    return gaps, missing


def _require_utc(value: datetime) -> None:
    """Reject naive or non-UTC instants at the market-data boundary."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        message = "Candle timestamps must be timezone-aware UTC instants."
        raise CandleQualityError(message)
