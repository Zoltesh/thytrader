"""Exact prospective order economics shared by research and runtime entry gates."""

from decimal import Context, Decimal, localcontext
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from thytrader.execution.models import PositionSide


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


class EconomicPreflightRequest(BaseModel):
    """Read-only modeled entry and exit prices; rates are supplied assumptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    side: Literal["long", "short"] = "long"
    entry_price: EconomicDecimal
    quantity: EconomicDecimal = "1"
    stop_price: EconomicDecimal
    target_price: EconomicDecimal | None = None
    maker_fee_rate: EconomicDecimal
    taker_fee_rate: EconomicDecimal
    fixed_slippage_bps: EconomicDecimal = "0"
    spread_bps: EconomicDecimal = "0"

    @field_validator("entry_price", "quantity", "stop_price", "target_price")
    @classmethod
    def positive_price(cls, value: str | None) -> str | None:
        """Reject zero prices and quantities at the HTTP boundary."""
        if value is not None and Decimal(value) <= 0:
            raise ValueError("prices and quantity must be positive")
        return value

    @field_validator("maker_fee_rate", "taker_fee_rate")
    @classmethod
    def bounded_fee(cls, value: str) -> str:
        """Use the same supported ten-percent fee bound as research."""
        if Decimal(value) > Decimal("0.1"):
            raise ValueError("fee rates must be at most 0.1")
        return value

    @field_validator("fixed_slippage_bps", "spread_bps")
    @classmethod
    def bounded_stress(cls, value: str) -> str:
        """Reject stress assumptions beyond the supported research bound."""
        if Decimal(value) > 1000:
            raise ValueError("stress basis points must be at most 1000")
        return value


class EconomicPreflight(BaseModel):
    """Modeled quote PnL including both fees, with no submission or fill guarantee."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entry_notional: str
    entry_fee: str
    maker_break_even_price: str
    net_target_quote_pnl: str | None
    net_target_return_fraction: str | None
    net_stop_quote_pnl: str
    target_clears_costs: bool | None
    assumptions: Literal["maker_entry_maker_target_taker_stop"] = (
        "maker_entry_maker_target_taker_stop"
    )


def net_target_return(
    *, side: Literal["long", "short"] | PositionSide, entry: Decimal, target: Decimal, fee: Decimal
) -> Decimal:
    """Return maker-target profit after entry and exit fees per unit entry notional."""
    with localcontext(Context(prec=64)):
        gross = target - entry if side == "long" else entry - target
        return (gross - (entry + target) * fee) / entry


def target_guard_allows(
    guard: EconomicEntryGuard | None,
    *,
    side: Literal["long", "short"] | PositionSide,
    entry: Decimal,
    target: Decimal | None,
    fee: Decimal,
) -> bool:
    """An enabled gate requires a known target; an omitted gate preserves behavior."""
    if guard is None:
        return True
    if target is None:
        return False
    return net_target_return(side=side, entry=entry, target=target, fee=fee) >= Decimal(
        guard.minimum_net_target_return_fraction
    )


def economic_preflight(request: EconomicPreflightRequest) -> EconomicPreflight:
    """Calculate maker TP and stressed taker stop economics without changing orders."""
    with localcontext(Context(prec=64)):
        return _calculate_preflight(request)


def _calculate_preflight(request: EconomicPreflightRequest) -> EconomicPreflight:
    """Compute prices and quote amounts under the shared simulator precision."""
    entry, quantity = Decimal(request.entry_price), Decimal(request.quantity)
    maker, taker = Decimal(request.maker_fee_rate), Decimal(request.taker_fee_rate)
    direction = Decimal(1) if request.side == "long" else Decimal(-1)
    stop = Decimal(request.stop_price)
    target = None if request.target_price is None else Decimal(request.target_price)
    if direction * (entry - stop) <= 0 or (
        target is not None and direction * (target - entry) <= 0
    ):
        raise ValueError("stop and target must lie on the correct sides of entry")
    stress = (Decimal(request.fixed_slippage_bps) + Decimal(request.spread_bps) / 2) / 10_000
    stop *= 1 - direction * stress
    stop_pnl = (direction * (stop - entry) - entry * maker - stop * taker) * quantity
    net = (
        None
        if target is None
        else net_target_return(side=request.side, entry=entry, target=target, fee=maker)
    )
    break_even = entry * (1 + direction * maker) / (1 - direction * maker)
    return EconomicPreflight(
        entry_notional=_decimal_text(entry * quantity),
        entry_fee=_decimal_text(entry * quantity * maker),
        maker_break_even_price=_decimal_text(break_even),
        net_target_quote_pnl=None if net is None else _decimal_text(net * entry * quantity),
        net_target_return_fraction=None if net is None else _decimal_text(net),
        net_stop_quote_pnl=_decimal_text(stop_pnl),
        target_clears_costs=None if net is None else net > 0,
    )
