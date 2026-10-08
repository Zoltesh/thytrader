"""Deterministic candle-based stresses; these are assumptions, not queue observations."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecutionStress(BaseModel):
    """Delay entries, require maker penetration, and cap entry fill with remainder canceled.

    Latency counts additional completed bars after the signal; normal execution already
    waits until the next bar. Penetration applies to entries and take-profits. A partial
    entry fills once at the modeled maker price and cancels the unfilled remainder.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    entry_latency_bars: int = Field(default=0, strict=True, ge=0, le=100)
    maker_penetration_bps: str = Field(
        default="0", strict=True, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
    )
    entry_fill_fraction: str = Field(
        default="1", strict=True, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
    )

    @field_validator("maker_penetration_bps")
    @classmethod
    def bounded_penetration(cls, value: str) -> str:
        """Keep the disclosed candle penetration within one thousand basis points."""
        if Decimal(value) > 1000:
            raise ValueError("maker_penetration_bps must be at most 1000")
        return value

    @field_validator("entry_fill_fraction")
    @classmethod
    def bounded_fill(cls, value: str) -> str:
        """Require a positive fraction no greater than the posted quantity."""
        if not 0 < Decimal(value) <= 1:
            raise ValueError("entry_fill_fraction must be greater than 0 and at most 1")
        return value


def maker_touched(
    *, high: Decimal, low: Decimal, price: Decimal, buy: bool, stress: ExecutionStress | None
) -> bool:
    """Require deterministic adverse penetration while executing at the posted limit."""
    buffer = Decimal(0) if stress is None else Decimal(stress.maker_penetration_bps) / 10_000
    return low <= price * (1 - buffer) if buy else high >= price * (1 + buffer)
