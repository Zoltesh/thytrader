"""Versioned Coinbase Advanced Trade spot maker/taker schedule for research suggestions.

This table pins the public 30-day USD volume maker-taker bands as captured 2026-09-13
from Coinbase's published Exchange / Advanced Trade fee pages. The bps match the in-repo
Coinbase adapter fixtures (Tier 1 = 40/60, Tier 2 = 25/40).

Research forms may prefill from this table. Live Coinbase billing is unchanged. Demo and
missing credentials must not map through it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile

COINBASE_SPOT_FEE_SCHEDULE_VERSION = "coinbase-advanced-spot-fees-v1"
COINBASE_SPOT_FEE_SCHEDULE_AS_OF = date(2026, 9, 13)


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CoinbaseSpotFeeBand(_FrozenModel):
    """One published 30-day USD volume band on the pinned Coinbase spot schedule.

    ``usd_volume_from`` is inclusive. ``usd_volume_to`` is exclusive. The final band
    uses ``usd_volume_to=None`` for an unbounded upper end.
    """

    tier_id: str = Field(min_length=1, max_length=32)
    usd_volume_from: Decimal = Field(ge=Decimal("0"))
    usd_volume_to: Decimal | None = Field(default=None, ge=Decimal("0"))
    maker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    taker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @model_validator(mode="after")
    def require_coherent_band(self) -> Self:
        """Reject inverted volume bounds or a maker rate above taker."""
        if self.usd_volume_to is not None and self.usd_volume_to <= self.usd_volume_from:
            raise ValueError("usd_volume_to must be greater than usd_volume_from")
        if self.maker_fee_rate > self.taker_fee_rate:
            raise ValueError("maker_fee_rate must not exceed taker_fee_rate")
        return self

    def contains_usd_volume(self, usd_volume_30d: Decimal) -> bool:
        """Return whether trailing 30-day USD volume falls in this band."""
        if usd_volume_30d < self.usd_volume_from:
            return False
        if self.usd_volume_to is None:
            return True
        return usd_volume_30d < self.usd_volume_to


class ResearchFeeSuggestion(_FrozenModel):
    """Research-only maker/taker suggestion derived from a fee-tier snapshot.

    Suggested rates are modeled CostAssumptions defaults, not observed Coinbase fills.
    """

    suggested_maker_fee_rate: Decimal | None = None
    suggested_taker_fee_rate: Decimal | None = None
    source: Literal["coinbase_fee_schedule", "unavailable"]
    unavailable_reason: Literal["demo_or_missing_credentials"] | None = None
    fee_tier: str | None = Field(default=None, min_length=1, max_length=64)
    schedule_tier_id: str | None = Field(default=None, min_length=1, max_length=32)
    schedule_version: str | None = Field(default=None, min_length=1, max_length=64)
    schedule_as_of: date | None = None
    fetched_at: datetime | None = None

    @model_validator(mode="after")
    def require_source_consistent_fields(self) -> Self:
        """Keep unavailable suggestions empty and schedule suggestions fully identified."""
        if self.source == "unavailable":
            if self.unavailable_reason is None:
                raise ValueError("unavailable suggestions require a reason")
            if any(
                value is not None
                for value in (
                    self.suggested_maker_fee_rate,
                    self.suggested_taker_fee_rate,
                    self.fee_tier,
                    self.schedule_tier_id,
                    self.schedule_version,
                    self.schedule_as_of,
                    self.fetched_at,
                )
            ):
                raise ValueError("unavailable suggestions must not invent schedule metadata")
            return self
        if self.unavailable_reason is not None:
            raise ValueError("schedule suggestions must not carry an unavailable reason")
        if (
            self.suggested_maker_fee_rate is None
            or self.suggested_taker_fee_rate is None
            or self.fee_tier is None
            or self.schedule_tier_id is None
            or self.schedule_version is None
            or self.schedule_as_of is None
            or self.fetched_at is None
        ):
            raise ValueError("schedule suggestions require rates and source metadata")
        return self


COINBASE_SPOT_FEE_BANDS: tuple[CoinbaseSpotFeeBand, ...] = (
    CoinbaseSpotFeeBand(
        tier_id="usd-0-10k",
        usd_volume_from=Decimal("0"),
        usd_volume_to=Decimal("10000"),
        maker_fee_rate=Decimal("0.0040"),
        taker_fee_rate=Decimal("0.0060"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-10k-50k",
        usd_volume_from=Decimal("10000"),
        usd_volume_to=Decimal("50000"),
        maker_fee_rate=Decimal("0.0025"),
        taker_fee_rate=Decimal("0.0040"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-50k-100k",
        usd_volume_from=Decimal("50000"),
        usd_volume_to=Decimal("100000"),
        maker_fee_rate=Decimal("0.0015"),
        taker_fee_rate=Decimal("0.0025"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-100k-1m",
        usd_volume_from=Decimal("100000"),
        usd_volume_to=Decimal("1000000"),
        maker_fee_rate=Decimal("0.0010"),
        taker_fee_rate=Decimal("0.0020"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-1m-15m",
        usd_volume_from=Decimal("1000000"),
        usd_volume_to=Decimal("15000000"),
        maker_fee_rate=Decimal("0.0008"),
        taker_fee_rate=Decimal("0.0018"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-15m-75m",
        usd_volume_from=Decimal("15000000"),
        usd_volume_to=Decimal("75000000"),
        maker_fee_rate=Decimal("0.0006"),
        taker_fee_rate=Decimal("0.0016"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-75m-250m",
        usd_volume_from=Decimal("75000000"),
        usd_volume_to=Decimal("250000000"),
        maker_fee_rate=Decimal("0.0003"),
        taker_fee_rate=Decimal("0.0010"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-250m-400m",
        usd_volume_from=Decimal("250000000"),
        usd_volume_to=Decimal("400000000"),
        maker_fee_rate=Decimal("0.0000"),
        taker_fee_rate=Decimal("0.0006"),
    ),
    CoinbaseSpotFeeBand(
        tier_id="usd-400m-plus",
        usd_volume_from=Decimal("400000000"),
        usd_volume_to=None,
        maker_fee_rate=Decimal("0.0000"),
        taker_fee_rate=Decimal("0.0004"),
    ),
)

_BANDS_BY_ID: dict[str, CoinbaseSpotFeeBand] = {
    band.tier_id: band for band in COINBASE_SPOT_FEE_BANDS
}
_TIER_NUMBER_TO_BAND_ID: dict[str, str] = {
    "1": "usd-0-10k",
    "2": "usd-10k-50k",
    "3": "usd-50k-100k",
    "4": "usd-100k-1m",
    "5": "usd-1m-15m",
    "6": "usd-15m-75m",
    "7": "usd-75m-250m",
    "8": "usd-250m-400m",
    "9": "usd-400m-plus",
}


def lookup_coinbase_spot_fee_band_by_volume(usd_volume_30d: Decimal) -> CoinbaseSpotFeeBand:
    """Return the published band for a non-negative 30-day USD volume."""
    if usd_volume_30d < 0:
        raise ValueError("30-day USD volume must be non-negative.")
    for band in COINBASE_SPOT_FEE_BANDS:
        if band.contains_usd_volume(usd_volume_30d):
            return band
    raise RuntimeError("Coinbase spot fee schedule has no matching volume band.")


def lookup_coinbase_spot_fee_band_by_label(fee_tier: str) -> CoinbaseSpotFeeBand | None:
    """Return the published band for a Coinbase ``pricing_tier`` label when known."""
    tokens = fee_tier.strip().lower().replace(",", "").split()
    if len(tokens) >= 2 and tokens[0] in {"tier", "advanced"}:
        number = tokens[1].strip("().")
        band_id = _TIER_NUMBER_TO_BAND_ID.get(number)
        if band_id is not None:
            return _BANDS_BY_ID[band_id]
    return None


def lookup_coinbase_spot_fee_band(*, fee_tier: str, usd_volume_30d: Decimal) -> CoinbaseSpotFeeBand:
    """Prefer a known Coinbase tier label, otherwise the 30-day volume band."""
    labeled = lookup_coinbase_spot_fee_band_by_label(fee_tier)
    if labeled is not None:
        return labeled
    return lookup_coinbase_spot_fee_band_by_volume(usd_volume_30d)


def suggest_research_fee_rates(*, profile: FeeProfile, demo: bool) -> ResearchFeeSuggestion:
    """Map a live fee-tier snapshot through the pinned schedule, or fail closed in demo."""
    if demo:
        return ResearchFeeSuggestion(
            source="unavailable",
            unavailable_reason="demo_or_missing_credentials",
        )
    band = lookup_coinbase_spot_fee_band(
        fee_tier=profile.fee_tier,
        usd_volume_30d=profile.usd_volume_30d,
    )
    return ResearchFeeSuggestion(
        suggested_maker_fee_rate=band.maker_fee_rate,
        suggested_taker_fee_rate=band.taker_fee_rate,
        source="coinbase_fee_schedule",
        fee_tier=profile.fee_tier,
        schedule_tier_id=band.tier_id,
        schedule_version=COINBASE_SPOT_FEE_SCHEDULE_VERSION,
        schedule_as_of=COINBASE_SPOT_FEE_SCHEDULE_AS_OF,
        fetched_at=profile.as_of,
    )
