"""Provider-neutral historical market-data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

# Coinbase Advanced Trade pages at most ~350 candles; this caps one bounded request.
# 25,920 five-minute bars is 90 days; 25,920 hourly bars is 1,080 days, but 1h
# watches stay min(requested, 2,160 hours) via the existing lookback maximum.
MAX_HISTORICAL_INTERVAL_COUNT = 25_920


class CandleInterval(StrEnum):
    """Supported closed-candle intervals for research datasets and paper execution."""

    ONE_HOUR = "1h"
    FIVE_MINUTES = "5m"

    @property
    def duration(self) -> timedelta:
        """Return the exact duration represented by one interval."""
        if self is CandleInterval.ONE_HOUR:
            return timedelta(hours=1)
        if self is CandleInterval.FIVE_MINUTES:
            return timedelta(minutes=5)
        message = f"Unsupported candle interval: {self.value}."
        raise ValueError(message)

    def align_closed_end(self, now: datetime) -> datetime:
        """Return the exclusive end of the latest fully closed bar at ``now``."""
        instant = now.astimezone(UTC).replace(second=0, microsecond=0)
        if self is CandleInterval.ONE_HOUR:
            return instant.replace(minute=0)
        if self is CandleInterval.FIVE_MINUTES:
            minute = (instant.minute // 5) * 5
            return instant.replace(minute=minute)
        message = f"Unsupported candle interval: {self.value}."
        raise ValueError(message)

    @property
    def execution_supported(self) -> bool:
        """Paper evaluates closed 1h or 5m bars; live remains 1h-only at the deployment gate."""
        return self in {CandleInterval.ONE_HOUR, CandleInterval.FIVE_MINUTES}


def parse_candle_interval(value: str) -> CandleInterval:
    """Parse a supported interval token or fail closed."""
    try:
        return CandleInterval(value)
    except ValueError as error:
        message = f"Unsupported candle interval: {value}."
        raise ValueError(message) from error


def interval_from_range(report: CandleRangeReport) -> CandleInterval:
    """Infer the supported interval from one half-open range's requested span."""
    if report.requested_candle_count < 1:
        raise ValueError("A dataset range must request at least one candle.")
    step = (report.ends_at - report.starts_at) / report.requested_candle_count
    for interval in CandleInterval:
        if interval.duration == step:
            return interval
    raise ValueError("Historical range does not match a supported candle interval.")


@dataclass(frozen=True, slots=True)
class Candle:
    """One exact OHLCV candle identified by its UTC opening instant."""

    starts_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class MarketProduct:
    """One tradable spot product with exact venue constraints."""

    product_id: str
    base_currency: str
    quote_currency: str
    price_increment: Decimal
    base_increment: Decimal
    quote_increment: Decimal
    base_min_size: Decimal
    quote_min_size: Decimal
    trading_enabled: bool


@dataclass(frozen=True, slots=True)
class CandleQualityReport:
    """Validated completed candles plus observable completeness and freshness facts."""

    candles: tuple[Candle, ...]
    candle_count: int
    gap_count: int
    missing_intervals: int
    latest_completed_at: datetime | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class CandleRangeReport:
    """Validated candle quality facts compared with one explicit half-open UTC range."""

    starts_at: datetime
    ends_at: datetime
    requested_candle_count: int
    quality: CandleQualityReport
    complete: bool


@dataclass(frozen=True, slots=True)
class MarketDataPreview:
    """A point-in-time product and validated-candle quality observation."""

    product: MarketProduct
    interval: CandleInterval
    as_of: datetime
    quality: CandleQualityReport
