"""Declared economic entry hurdle carried by strategy rules.

Strategies declare the guard and execution enforces it, so the declaration lives
with the strategy model and imports nothing from execution.
``thytrader.execution.economics`` re-exports these names.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _decimal_text(value: Decimal) -> str:
    """Render exact finite values without exponent notation or trailing zeroes."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


EconomicDecimal = Annotated[
    str, Field(strict=True, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", max_length=80)
]


class EconomicEntryGuard(BaseModel):
    """Require a declared maker target to clear this net return on entry notional."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    minimum_net_target_return_fraction: EconomicDecimal

    @field_validator("minimum_net_target_return_fraction")
    @classmethod
    def bound_return(cls, value: str) -> str:
        """Bound the requested net hurdle and normalize fingerprinted decimals."""
        if Decimal(value) > 1:
            raise ValueError("minimum_net_target_return_fraction must be at most 1")
        return _decimal_text(Decimal(value))
