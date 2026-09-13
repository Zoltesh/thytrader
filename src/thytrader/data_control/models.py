"""Confirmation-gated market-data watchlist and ingest mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from thytrader.market_data.models import (
    DATASET_TIMEFRAME_PATTERN,
    CandleInterval,
    parse_candle_interval,
)

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

    product_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USD$")
    timeframe: str = Field(pattern=DATASET_TIMEFRAME_PATTERN)
    lookback_hours: int = Field(default=168, ge=1, le=2_160)
    enabled: bool = True


class IngestRequest(_FrozenModel):
    """Run one complete-only ingest for a watched or named target."""

    product_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USD$")
    timeframe: str = Field(pattern=DATASET_TIMEFRAME_PATTERN)


@dataclass(frozen=True, slots=True)
class GapObservation:
    """One missing bar start with a classified cause."""

    starts_at: datetime
    cause: GapCause


def require_interval(value: str) -> CandleInterval:
    """Parse a dataset timeframe (1h, 5m, or 15m) or fail closed."""
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
