"""Provider-neutral historical market-data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from decimal import Decimal

# Coinbase Advanced Trade pages at most ~350 candles; this caps one bounded request.
# 129,600 one-minute bars is 90 days; 25,920 five-minute bars is also 90 days.
# 129,600 hourly bars is 5,400 days, but 1h watches stay min(requested, 2,160 hours)
# via the existing lookback maximum.
MAX_HISTORICAL_INTERVAL_COUNT = 129_600

DatasetTimeframe = Literal["1h", "5m", "15m", "30m", "6h", "1d", "1m", "2h", "4h"]
DATASET_TIMEFRAMES: tuple[DatasetTimeframe, ...] = (
    "1h",
    "5m",
    "15m",
    "30m",
    "6h",
    "1d",
    "1m",
    "2h",
    "4h",
)
DATASET_TIMEFRAME_PATTERN = r"^(1h|5m|15m|30m|6h|1d|1m|2h|4h)$"


class CandleInterval(StrEnum):
    """Closed-candle intervals for complete-only historical datasets.

    Dataset ingest, catalog, and verification accept every Coinbase-listed
    Advanced Trade candle granularity. Paper still evaluates only 1h or 5m;
    live accepts 1h or 5m at the deployment gate.
    """

    ONE_HOUR = "1h"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    SIX_HOURS = "6h"
    ONE_DAY = "1d"
    ONE_MINUTE = "1m"
    TWO_HOURS = "2h"
    FOUR_HOURS = "4h"

    @property
    def duration(self) -> timedelta:
        """Return the exact duration represented by one interval."""
        try:
            return _INTERVAL_DURATIONS[self]
        except KeyError as error:
            message = f"Unsupported candle interval: {self.value}."
            raise ValueError(message) from error

    def align_closed_end(self, now: datetime) -> datetime:
        """Return the exclusive end of the latest fully closed bar at ``now``."""
        instant = now.astimezone(UTC).replace(second=0, microsecond=0)
        minutes = int(self.duration.total_seconds() // 60)
        if minutes < 1:
            message = f"Unsupported candle interval: {self.value}."
            raise ValueError(message)
        if minutes < 60:
            minute = (instant.minute // minutes) * minutes
            return instant.replace(minute=minute)
        hours = minutes // 60
        if hours < 24:
            hour = (instant.hour // hours) * hours
            return instant.replace(hour=hour, minute=0)
        if hours == 24:
            return instant.replace(hour=0, minute=0)
        message = f"Unsupported candle interval: {self.value}."
        raise ValueError(message)

    @property
    def execution_supported(self) -> bool:
        """Paper and live evaluate closed 1h or 5m bars."""
        return self in {CandleInterval.ONE_HOUR, CandleInterval.FIVE_MINUTES}


_INTERVAL_DURATIONS: dict[CandleInterval, timedelta] = {
    CandleInterval.ONE_MINUTE: timedelta(minutes=1),
    CandleInterval.FIVE_MINUTES: timedelta(minutes=5),
    CandleInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
    CandleInterval.THIRTY_MINUTES: timedelta(minutes=30),
    CandleInterval.ONE_HOUR: timedelta(hours=1),
    CandleInterval.TWO_HOURS: timedelta(hours=2),
    CandleInterval.FOUR_HOURS: timedelta(hours=4),
    CandleInterval.SIX_HOURS: timedelta(hours=6),
    CandleInterval.ONE_DAY: timedelta(days=1),
}


def parse_candle_interval(value: str) -> CandleInterval:
    """Parse a supported interval token or fail closed."""
    try:
        return CandleInterval(value)
    except ValueError as error:
        message = f"Unsupported candle interval: {value}."
        raise ValueError(message) from error


def as_dataset_timeframe(interval: CandleInterval) -> DatasetTimeframe:
    """Narrow a candle interval to the dataset catalog token."""
    for token in DATASET_TIMEFRAMES:
        if interval.value == token:
            return token
    message = f"Unsupported candle interval: {interval.value}."
    raise ValueError(message)


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
