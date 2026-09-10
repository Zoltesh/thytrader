"""Typed contracts and evaluator for market data candle freshness."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.market_data.models import CandleInterval


class FreshnessStatus(StrEnum):
    """Normalized freshness status for market data candles."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


# Two expected intervals plus a five-minute grace period.
_FRESHNESS_GRACE_SECONDS = 300


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketDataFreshness(_FrozenModel):
    """Explicit freshness state derived from newest verified candle age."""

    product_id: str = Field(min_length=1, max_length=32)
    newest_candle_at: datetime | None = None
    as_of: datetime
    age_seconds: int | None = None
    status: FreshnessStatus

    @field_validator("as_of", "newest_candle_at")
    @classmethod
    def require_utc_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject naive datetimes to guarantee deterministic interval evaluation."""
        if value is not None and value.tzinfo is not UTC:
            raise ValueError("datetime must be timezone-aware UTC")
        return value


def evaluate_freshness(
    *,
    product_id: str,
    newest_candle_at: datetime | None,
    now: datetime,
    interval: CandleInterval = CandleInterval.ONE_HOUR,
) -> MarketDataFreshness:
    """Evaluate candle freshness deterministically against UTC observation time."""
    if now.tzinfo is not UTC:
        raise ValueError("now must be timezone-aware UTC")

    if newest_candle_at is None:
        return MarketDataFreshness(
            product_id=product_id,
            newest_candle_at=None,
            as_of=now,
            age_seconds=None,
            status=FreshnessStatus.UNKNOWN,
        )

    if newest_candle_at.tzinfo is not UTC:
        raise ValueError("newest_candle_at must be timezone-aware UTC")

    threshold = int(interval.duration.total_seconds() * 2) + _FRESHNESS_GRACE_SECONDS
    age = int((now - newest_candle_at).total_seconds())
    if age < 0:
        # Candle timestamp is in the future - fail safe to stale/unknown
        status = FreshnessStatus.STALE
    elif age < threshold:
        status = FreshnessStatus.FRESH
    else:
        status = FreshnessStatus.STALE

    return MarketDataFreshness(
        product_id=product_id,
        newest_candle_at=newest_candle_at,
        as_of=now,
        age_seconds=max(0, age),
        status=status,
    )
