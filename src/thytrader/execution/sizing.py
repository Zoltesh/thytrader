"""ATR risk-fraction sizing for one long or short entry, quantized to venue increments.

Every refusal names an ``EntrySkipReason`` (ADR 0090). ``size_entry_or_skip`` and
``size_pyramid_add_or_skip`` return it; ``size_entry`` / ``size_pyramid_add`` keep the
older ``None`` contract for callers that only need the order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from thytrader.execution.geometry import EntryLevels, EntrySkipReason, entry_levels
from thytrader.execution.models import PositionSide
from thytrader.strategies.models import reward_risk_multiple

if TYPE_CHECKING:
    from thytrader.market_data.models import MarketProduct
    from thytrader.strategies.models import StrategyDefinition


@dataclass(frozen=True, slots=True)
class SizedEntry:
    """One executable entry size plus stop and optional take-profit prices.

    ``target_price`` is None when the strategy declares ``take_profit: {"kind": "none"}``.
    """

    quantity: Decimal
    notional: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal | None


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
    """Size a long or short, or return None when ``size_entry_or_skip`` names a reason."""
    sized = size_entry_or_skip(
        strategy=strategy,
        cash=cash,
        entry_price=entry_price,
        atr=atr,
        product=product,
        fee_rate=fee_rate,
        side=side,
    )
    return sized if isinstance(sized, SizedEntry) else None


def size_entry_or_skip(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    atr: Decimal,
    product: MarketProduct,
    fee_rate: Decimal = Decimal("0"),
    side: PositionSide = PositionSide.LONG,
) -> SizedEntry | EntrySkipReason:
    """Size a long or short using ATR stop distance, risk fraction, and quote bounds."""
    if entry_price <= 0:
        return EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE
    if atr <= 0:
        return EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE
    if cash <= 0:
        return EntrySkipReason.INSUFFICIENT_CASH
    snapped_entry = quantize_to_increment(entry_price, product.price_increment)
    levels = entry_levels(
        side=side,
        entry_price=snapped_entry,
        stop_distance=atr * Decimal(strategy.exits.initial_stop.multiple),
        reward_multiple=reward_risk_multiple(strategy.exits),
        price_increment=product.price_increment,
    )
    if isinstance(levels, EntrySkipReason):
        return levels
    return _bounded_order(
        strategy=strategy,
        cash=cash,
        entry_price=snapped_entry,
        levels=levels,
        product=product,
        fee_rate=fee_rate,
        side=side,
    )


def _bounded_order(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    levels: EntryLevels,
    product: MarketProduct,
    fee_rate: Decimal,
    side: PositionSide,
) -> SizedEntry | EntrySkipReason:
    """Risk-size against the realized stop, clamp to strategy/exposure/cash, check minimums."""
    realized_stop_distance = (
        (entry_price - levels.stop_price)
        if side is PositionSide.LONG
        else (levels.stop_price - entry_price)
    )
    if realized_stop_distance <= 0:
        return EntrySkipReason.STOP_WITHIN_PRICE_INCREMENT
    requested_risk = cash * Decimal(strategy.sizing.risk_fraction)
    risk_quantity = requested_risk / realized_stop_distance
    fee_adjusted_cash = cash / (Decimal("1") + fee_rate) if fee_rate > 0 else cash
    maximum_notional = min(
        Decimal(strategy.sizing.max_quote_notional),
        cash * Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction),
        fee_adjusted_cash,
    )
    notional = min(risk_quantity * entry_price, maximum_notional)
    if notional < Decimal(strategy.sizing.min_quote_notional):
        return EntrySkipReason.NOTIONAL_BELOW_MINIMUM
    quantity = quantize_to_increment(notional / entry_price, product.base_increment)
    if quantity < product.base_min_size:
        return EntrySkipReason.QUANTITY_BELOW_VENUE_MINIMUM
    notional = quantity * entry_price
    if notional < product.quote_min_size:
        return EntrySkipReason.NOTIONAL_BELOW_VENUE_MINIMUM
    if side is PositionSide.LONG and notional > cash:
        return EntrySkipReason.INSUFFICIENT_CASH
    return SizedEntry(
        quantity=quantity,
        notional=notional,
        entry_price=entry_price,
        stop_price=levels.stop_price,
        target_price=levels.target_price,
    )


def size_pyramid_add(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    existing_stop: Decimal,
    existing_target: Decimal | None,
    product: MarketProduct,
    fee_rate: Decimal = Decimal("0"),
    side: PositionSide = PositionSide.LONG,
) -> SizedEntry | None:
    """Size a same-side add, or return None when ``size_pyramid_add_or_skip`` names a reason."""
    sized = size_pyramid_add_or_skip(
        strategy=strategy,
        cash=cash,
        entry_price=entry_price,
        existing_stop=existing_stop,
        existing_target=existing_target,
        product=product,
        fee_rate=fee_rate,
        side=side,
    )
    return sized if isinstance(sized, SizedEntry) else None


def size_pyramid_add_or_skip(
    *,
    strategy: StrategyDefinition,
    cash: Decimal,
    entry_price: Decimal,
    existing_stop: Decimal,
    existing_target: Decimal | None,
    product: MarketProduct,
    fee_rate: Decimal = Decimal("0"),
    side: PositionSide = PositionSide.LONG,
) -> SizedEntry | EntrySkipReason:
    """Size a same-side add from remaining cash against the existing stop.

    Stop and target are copied, never recomputed, so they cannot worsen.
    """
    if entry_price <= 0:
        return EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE
    if cash <= 0:
        return EntrySkipReason.INSUFFICIENT_CASH
    snapped_entry = quantize_to_increment(entry_price, product.price_increment)
    if snapped_entry <= 0:
        return EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE
    realized_stop_distance = (
        (snapped_entry - existing_stop)
        if side is PositionSide.LONG
        else (existing_stop - snapped_entry)
    )
    if realized_stop_distance <= 0:
        return EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE
    return _bounded_order(
        strategy=strategy,
        cash=cash,
        entry_price=snapped_entry,
        levels=EntryLevels(stop_price=existing_stop, target_price=existing_target),
        product=product,
        fee_rate=fee_rate,
        side=side,
    )
