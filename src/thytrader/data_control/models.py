"""Confirmation-gated market-data watchlist and ingest mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from thytrader.market_data.lookback import validate_watch_lookback_hours
from thytrader.market_data.models import (
    DATASET_TIMEFRAME_PATTERN,
    CandleInterval,
    parse_candle_interval,
)
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN

if TYPE_CHECKING:
    from datetime import datetime


class DataControlError(RuntimeError):
    """Report a redacted data-control failure without trading authority."""


class GapCause(StrEnum):
    """Why a requested bar is absent from local complete coverage."""

    NOT_FETCHED = "not_fetched"
    EXCHANGE_UNAVAILABLE = "exchange_unavailable"
    INCOMPLETE_LOCAL = "incomplete_local"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WatchTargetRequest(_FrozenModel):
    """Add or replace one ingestion watch target."""

    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    timeframe: str = Field(pattern=DATASET_TIMEFRAME_PATTERN)
    lookback_hours: int = Field(default=168, ge=1)
    enabled: bool = True

    def model_post_init(self, __context: object) -> None:
        """Reject lookbacks above the interval-specific ceiling."""
        try:
            validate_watch_lookback_hours(require_interval(self.timeframe), self.lookback_hours)
        except ValueError as error:
            raise ValueError(str(error)) from error


class IngestRequest(_FrozenModel):
    """Run one complete-only ingest for a watched or named target."""

    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    timeframe: str = Field(pattern=DATASET_TIMEFRAME_PATTERN)


@dataclass(frozen=True, slots=True)
class GapObservation:
    """One missing bar start with a classified cause."""

    starts_at: datetime
    cause: GapCause


@dataclass(frozen=True, slots=True)
class GapInspection:
    """Watch-window gap classification without interpolation."""

    starts_at: datetime
    ends_at: datetime
    gaps: tuple[GapObservation, ...]
    gap_summary: dict[str, int]
    warning: str | None
    lookback_hours: int
    complete: bool
    watch_complete: bool
    truncated: bool = False
    scanned_bar_count: int = 0


def require_interval(value: str) -> CandleInterval:
    """Parse a dataset timeframe (1h, 5m, 15m, 30m, 6h, 1d, 1m, 2h, or 4h) or fail closed."""
    try:
        interval = parse_candle_interval(value)
    except ValueError as error:
        raise DataControlError(str(error)) from error
    return interval


def classify_gap(
    *,
    present_locally: bool,
    present_on_exchange: bool | None,
    worker_attempted: bool,
    worker_complete: bool,
) -> GapCause | None:
    """Classify one expected bar that may be missing from local complete data."""
    if present_locally:
        return None
    if present_on_exchange is False:
        return GapCause.EXCHANGE_UNAVAILABLE
    if worker_attempted and not worker_complete:
        return GapCause.INCOMPLETE_LOCAL
    return GapCause.NOT_FETCHED
