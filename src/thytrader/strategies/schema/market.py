"""Traded instruments, read-only reference instruments, and data requirements."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from thytrader.market_data.models import DatasetTimeframe
from thytrader.market_data.products import (
    SPOT_PRODUCT_ID_PATTERN,
    SpotQuoteCurrency,
    parse_spot_product_id,
)
from thytrader.strategies.schema.primitives import _FrozenModel


class Instrument(_FrozenModel):
    """One conservative Coinbase USD or USDC spot instrument."""

    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    base_currency: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    quote_currency: SpotQuoteCurrency

    @model_validator(mode="after")
    def validate_product_components(self) -> Self:
        """Require the product identifier to match its explicit currencies."""
        if self.product_id != f"{self.base_currency}-{self.quote_currency}":
            raise ValueError("product_id must match base_currency and quote_currency")
        return self


MAX_REFERENCE_INSTRUMENTS = 3
"""Most read-only reference series one strategy document may declare (ADR 0096)."""

REFERENCE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class ReferenceInstrument(_FrozenModel):
    """One read-only cross-instrument series that indicators may declare as their ``source``.

    A reference is never traded and never receives orders: it only feeds indicator values
    (for example a BTC-USDC 1d regime gate on an alt strategy; ADR 0096). Its bars align like
    an HTF clock: at each decision close only the last reference bar that has already closed
    is visible, so a reference bar is used only after it closes.
    """

    id: str = Field(pattern=REFERENCE_ID_PATTERN)
    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    timeframe: DatasetTimeframe

    @property
    def base_currency(self) -> str:
        """Base currency of the reference product (``BTC`` for ``BTC-USDC``)."""
        return parse_spot_product_id(self.product_id)[0]

    @property
    def quote_currency(self) -> SpotQuoteCurrency:
        """Quote currency of the reference product (``USDC`` for ``BTC-USDC``)."""
        return parse_spot_product_id(self.product_id)[1]


class DataRequirements(_FrozenModel):
    """Historical inputs required before strategy evaluation can begin.

    ``reference_instruments`` (ADR 0096) declares read-only cross-instrument series. It is
    omitted from canonical JSON when empty, so documents without references keep their
    canonical bytes and fingerprints. Each reference's warmup and OHLCV fields are derived
    from the indicators whose ``source`` names it; ``warmup_bars`` and ``required_fields``
    describe the traded instrument's decision clock only.
    """

    warmup_bars: int = Field(ge=1, le=10_000)
    required_fields: tuple[Literal["open", "high", "low", "close", "volume"], ...] = Field(
        min_length=1,
        max_length=5,
    )
    reference_instruments: tuple[ReferenceInstrument, ...] = Field(
        default=(),
        max_length=MAX_REFERENCE_INSTRUMENTS,
        exclude_if=lambda value: not value,
        description=(
            "Optional read-only reference series (1-3) that indicators may read with "
            "`source: <id>`. Never traded. Omitted when empty."
        ),
    )

    @field_validator("required_fields")
    @classmethod
    def require_unique_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate candle-field declarations."""
        if len(value) != len(set(value)):
            raise ValueError("required_fields must be unique")
        return value

    @model_validator(mode="after")
    def require_unique_references(self) -> Self:
        """Reject duplicate reference ids and duplicate product/timeframe series."""
        identifiers = [reference.id for reference in self.reference_instruments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("reference_instruments ids must be unique")
        series = [
            (reference.product_id, reference.timeframe) for reference in self.reference_instruments
        ]
        if len(series) != len(set(series)):
            raise ValueError(
                "reference_instruments must not repeat one product_id and timeframe pair"
            )
        return self


class TimeframeDataRequirement(_FrozenModel):
    """One product timeframe a published strategy must bind for research."""

    timeframe: str
    warmup_bars: int
    required_fields: tuple[Literal["open", "high", "low", "close", "volume"], ...]
    role: Literal["decision", "filter", "indicator"]


class ReferenceDataRequirement(_FrozenModel):
    """One reference series a strategy reads, with the warmup and fields its indicators need."""

    reference_id: str
    product_id: str
    timeframe: DatasetTimeframe
    warmup_bars: int
    required_fields: tuple[Literal["open", "high", "low", "close", "volume"], ...]
