"""Provider-neutral historical market-data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal


class CandleInterval(StrEnum):
    """Supported closed-candle intervals for the initial market-data preview."""

    ONE_HOUR = "1h"

    @property
    def duration(self) -> timedelta:
        """Return the exact duration represented by one interval."""
        return timedelta(hours=1)


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
