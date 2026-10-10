"""Futures fields of the research run specification (ADR 0128).

Each is excluded from the canonical specification while unset, so spot run specs and their
fingerprints are byte-identical. A futures run binds the exact contract (from a fingerprinted
catalog observation), a constant margin assumption (no margin history exists), and the
funding series it consumed or an explicit, disclosed constant rate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, field_serializer, field_validator, model_validator

from thytrader.decimal_text import canonical_decimal
from thytrader.market_data.instrument_ids import FUTURES_PRODUCT_ID_PATTERN
from thytrader.strategies.schema.primitives import DecimalText, _FrozenModel

if TYPE_CHECKING:
    from collections.abc import Mapping

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MAX_STRESS_MULTIPLIER = Decimal(5)
_MAX_FEE_PER_CONTRACT = Decimal(100)
_MAX_FUNDING_RATE = Decimal("0.01")


def _utc(value: datetime | None, label: str) -> datetime | None:
    """Require timezone-aware UTC instants."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    """Canonical ``Z`` instant text."""
    return value.isoformat().replace("+00:00", "Z")


class InstrumentContract(_FrozenModel):
    """The futures contract a run binds, copied from one catalog observation.

    ``expires_at`` is ``null`` for a perp-style contract (its venue expiry is a far-future
    sentinel); ``listed_expiry`` is the day encoded in the id.
    """

    product_id: str = Field(pattern=FUTURES_PRODUCT_ID_PATTERN)
    kind: Literal["dated_future", "perpetual_future"]
    underlying: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    contract_size: DecimalText
    settlement_currency: Literal["USD"] = "USD"
    expires_at: datetime | None = None
    listed_expiry: date
    catalog_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @field_validator("contract_size")
    @classmethod
    def require_positive_size(cls, value: str) -> str:
        """A contract has a positive underlying quantity."""
        if Decimal(value) <= 0:
            raise ValueError("contract_size must be greater than 0")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_utc_expiry(cls, value: datetime | None) -> datetime | None:
        """Keep the expiry instant UTC."""
        return _utc(value, "expires_at")

    @field_serializer("expires_at", when_used="json")
    def serialize_expiry(self, value: datetime | None) -> str | None:
        """Serialize the expiry canonically."""
        return None if value is None else _utc_text(value)

    @model_validator(mode="after")
    def require_expiry_for_dated(self) -> Self:
        """A dated contract has an expiry; a perp does not."""
        if (self.kind == "dated_future") != (self.expires_at is not None):
            raise ValueError("expires_at is required for dated contracts and null for perps")
        return self


class MarginAssumption(_FrozenModel):
    """The constant initial-margin rates a run assumes (overnight, ADR 0128).

    ``source`` ``latest_observation`` names the catalog observation instant; ``explicit``
    rates are operator input. Rates are multiplied by ``stress_multiplier`` (≥ 1).
    Maintenance is ``maintenance_fraction_of_initial`` x initial (1.0 is conservative).
    An entry must leave (equity - maintenance) / equity ≥
    ``min_liquidation_buffer_fraction``.
    """

    long_rate: DecimalText
    short_rate: DecimalText
    source: Literal["latest_observation", "explicit"]
    observed_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    stress_multiplier: DecimalText = "1"
    maintenance_fraction_of_initial: DecimalText = "1"
    min_liquidation_buffer_fraction: DecimalText = "0.5"

    @field_validator("long_rate", "short_rate", "maintenance_fraction_of_initial")
    @classmethod
    def require_unit_rate(cls, value: str) -> str:
        """Rates and the maintenance fraction lie in (0, 1]."""
        rate = Decimal(value)
        if rate <= 0 or rate > 1:
            raise ValueError("margin rates must be greater than 0 and at most 1")
        return value

    @field_validator("stress_multiplier")
    @classmethod
    def require_stress(cls, value: str) -> str:
        """Stress never lowers margin and stays bounded."""
        multiplier = Decimal(value)
        if multiplier < 1 or multiplier > _MAX_STRESS_MULTIPLIER:
            raise ValueError("stress_multiplier must be between 1 and 5")
        return value

    @field_validator("min_liquidation_buffer_fraction")
    @classmethod
    def require_buffer(cls, value: str) -> str:
        """The buffer fraction lies in [0, 1)."""
        fraction = Decimal(value)
        if fraction < 0 or fraction >= 1:
            raise ValueError("min_liquidation_buffer_fraction must be in [0, 1)")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_utc_observation(cls, value: datetime | None) -> datetime | None:
        """Keep the observation instant UTC."""
        return _utc(value, "observed_at")

    @field_serializer("observed_at", when_used="json")
    def serialize_observed(self, value: datetime | None) -> str | None:
        """Serialize the observation instant canonically."""
        return None if value is None else _utc_text(value)

    @model_validator(mode="after")
    def require_observation_source(self) -> Self:
        """An observed assumption names its instant; an explicit one does not."""
        if (self.source == "latest_observation") != (self.observed_at is not None):
            raise ValueError("observed_at is required exactly for latest_observation margins")
        return self


class FundingAssumption(_FrozenModel):
    """The funding a perp run applies: a recorded series, or a disclosed constant rate.

    ``series_fingerprint`` identifies the settled hourly rows consumed (``settled_hours``
    of them). ``constant_rate`` is per hour and listed in the result's validity limits.
    Exactly one is set.
    """

    series_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN, exclude_if=lambda value: value is None
    )
    settled_hours: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    constant_rate: DecimalText | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("constant_rate")
    @classmethod
    def require_bounded_rate(cls, value: str | None) -> str | None:
        """A constant hourly rate is bounded to ±1%."""
        if value is not None and abs(Decimal(value)) > _MAX_FUNDING_RATE:
            raise ValueError("constant_rate must be within ±0.01 per hour")
        return value

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> Self:
        """Either the recorded series (with its hour count) or a constant rate."""
        series = self.series_fingerprint is not None
        if series == (self.constant_rate is not None):
            raise ValueError("set exactly one of series_fingerprint and constant_rate")
        if series != (self.settled_hours is not None):
            raise ValueError("settled_hours accompanies series_fingerprint")
        return self


def validate_fee_per_contract(value: str | None) -> str | None:
    """A per-contract fee, when set, is in [0, 100] USD."""
    if value is not None and not Decimal(0) <= Decimal(value) <= _MAX_FEE_PER_CONTRACT:
        raise ValueError("fee_per_contract must be between 0 and 100")
    return value


def funding_series_fingerprint(product_id: str, rates: Mapping[datetime, Decimal]) -> str:
    """Identify the settled hourly funding rows one perp run consumed (ADR 0128).

    The identity covers the product and every ``(funding hour, rate)`` pair in hour order,
    with canonical decimal text, so it never passes through binary floating point.
    """
    rows = [
        [_utc_text(hour.astimezone(UTC)), canonical_decimal(rate)]
        for hour, rate in sorted(rates.items())
    ]
    canonical = json.dumps(
        {"product_id": product_id, "rates": rows}, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + sha256(canonical.encode()).hexdigest()
