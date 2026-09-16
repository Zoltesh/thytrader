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


def _stop_and_target(
    *,
    side: PositionSide,
    entry_price: Decimal,
    stop_distance: Decimal,
    take_profit_multiple: Decimal,
    price_increment: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """Return quantized stop and take-profit, or None when geometry is illegal."""
    if side is PositionSide.LONG:
        stop_price = quantize_to_increment(entry_price - stop_distance, price_increment)
        target_price = quantize_to_increment(
            entry_price + stop_distance * take_profit_multiple,
            price_increment,
            rounding=ROUND_HALF_UP,
        )
        if stop_price <= 0 or target_price <= entry_price:
            return None
        return stop_price, target_price
    stop_price = quantize_to_increment(
        entry_price + stop_distance,
        price_increment,
        rounding=ROUND_HALF_UP,
    )
    target_price = quantize_to_increment(
        entry_price - stop_distance * take_profit_multiple,
        price_increment,
    )
    if target_price <= 0 or stop_price <= entry_price:
        return None
    return stop_price, target_price


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
    snapped_entry = quantize_to_increment(entry_price, product.price_increment)
    if snapped_entry <= 0:
        return None
    stop_distance = atr * Decimal(strategy.exits.initial_stop.multiple)
    if stop_distance <= 0:
        return None
    levels = _stop_and_target(
        side=side,
        entry_price=snapped_entry,
        stop_distance=stop_distance,
        take_profit_multiple=Decimal(strategy.exits.take_profit.multiple),
        price_increment=product.price_increment,
    )
    if levels is None:
        return None
    stop_price, target_price = levels
    realized_stop_distance = (
        (snapped_entry - stop_price)
        if side is PositionSide.LONG
        else (stop_price - snapped_entry)
    )
    if realized_stop_distance <= 0:
        return None
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / realized_stop_distance
    fee_adjusted_cash = cash / (Decimal("1") + fee_rate) if fee_rate > 0 else cash
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        fee_adjusted_cash,
    )
    notional = min(risk_quantity * snapped_entry, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None
    quantity = quantize_to_increment(notional / snapped_entry, product.base_increment)
    if quantity < product.base_min_size:
        return None
    notional = quantity * snapped_entry
    if notional < product.quote_min_size:
        return None
    if side is PositionSide.LONG and notional > cash:
        return None
    return SizedEntry(
        quantity=quantity,
        notional=notional,
        entry_price=snapped_entry,
        stop_price=stop_price,
        target_price=target_price,
    )


def size_pyramid_add(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    existing_stop: Decimal,
    existing_target: Decimal,
    product: MarketProduct,
    fee_rate: Decimal = Decimal("0"),
    side: PositionSide = PositionSide.LONG,
) -> SizedEntry | None:
    """Size a same-side add from remaining cash against the existing stop.

    Stop and target are copied, never recomputed, so they cannot worsen.
    """
    if entry_price <= 0 or cash <= 0:
        return None
    snapped_entry = quantize_to_increment(entry_price, product.price_increment)
    if snapped_entry <= 0:
        return None
    realized_stop_distance = (
        (snapped_entry - existing_stop)
        if side is PositionSide.LONG
        else (existing_stop - snapped_entry)
    )
    if realized_stop_distance <= 0:
        return None
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / realized_stop_distance
    fee_adjusted_cash = cash / (Decimal("1") + fee_rate) if fee_rate > 0 else cash
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        fee_adjusted_cash,
    )
    notional = min(risk_quantity * snapped_entry, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return None
    quantity = quantize_to_increment(notional / snapped_entry, product.base_increment)
    if quantity < product.base_min_size:
        return None
    notional = quantity * snapped_entry
    if notional < product.quote_min_size:
        return None
    if side is PositionSide.LONG and notional > cash:
        return None
    return SizedEntry(
        quantity=quantity,
        notional=notional,
        entry_price=snapped_entry,
        stop_price=existing_stop,
        target_price=existing_target,
    )
