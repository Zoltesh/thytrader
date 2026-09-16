"""ATR risk-fraction sizing for one long or short entry, quantized to venue increments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from thytrader.execution.models import PositionSide

if TYPE_CHECKING:
    from thytrader.market_data.models import MarketProduct
    from thytrader.strategies.models import StrategyDefinition


@dataclass(frozen=True, slots=True)
class SizedEntry:
    """One executable entry size plus stop and take-profit prices."""

    quantity: Decimal
    notional: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal


def quantize_to_increment(
    value: Decimal,
    increment: Decimal,
    *,
    rounding: str = ROUND_DOWN,
) -> Decimal:
    """Snap a price or quantity onto a positive venue increment."""
    if increment <= 0:
        raise ValueError("venue increment must be positive")
    steps = (value / increment).quantize(Decimal("1"), rounding=rounding)
    return steps * increment


def size_long_entry(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    atr: Decimal,
    product: MarketProduct,
    fee_rate: Decimal = Decimal("0"),
) -> SizedEntry | None:
    """Size a long using ATR stop distance, risk fraction, and quote bounds."""
    return size_entry(
        strategy=strategy,
        cash=cash,
        entry_price=entry_price,
        atr=atr,
        product=product,
        fee_rate=fee_rate,
        side=PositionSide.LONG,
    )


def size_entry(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    atr: Decimal,
    product: MarketProduct,
    fee_rate: Decimal = Decimal("0"),
    side: PositionSide = PositionSide.LONG,
) -> SizedEntry | None:
    """Size a long or short using ATR stop distance, risk fraction, and quote bounds."""
    if entry_price <= 0 or atr <= 0 or cash <= 0:
        return None
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    if stop_distance <= 0:
        return None
    if side is PositionSide.LONG:
        stop_price = quantize_to_increment(entry_price - stop_distance, product.price_increment)
        target_price = quantize_to_increment(
            entry_price + stop_distance * Decimal(strategy.exits.take_profit.multiple),
            product.price_increment,
            rounding=ROUND_HALF_UP,
        )
        if stop_price <= 0 or target_price <= entry_price:
            return None
    else:
        stop_price = quantize_to_increment(
            entry_price + stop_distance,
            product.price_increment,
            rounding=ROUND_HALF_UP,
        )
        target_price = quantize_to_increment(
            entry_price - stop_distance * Decimal(strategy.exits.take_profit.multiple),
            product.price_increment,
        )
        if target_price <= 0 or stop_price <= entry_price:
            return None
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / stop_distance
    fee_adjusted_cash = cash / (Decimal("1") + fee_rate) if fee_rate > 0 else cash
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        fee_adjusted_cash,
    )
    notional = min(risk_quantity * entry_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None
    quantity = quantize_to_increment(notional / entry_price, product.base_increment)
    if quantity < product.base_min_size:
        return None
    notional = quantity * entry_price
    if notional < product.quote_min_size:
        return None
    if side is PositionSide.LONG and notional > cash:
        return None
    snapped_entry = quantize_to_increment(entry_price, product.price_increment)
    if snapped_entry <= 0:
        return None
    return SizedEntry(
        quantity=quantity,
        notional=notional,
        entry_price=snapped_entry,
        stop_price=stop_price,
        target_price=target_price,
    )
