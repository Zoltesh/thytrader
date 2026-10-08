"""Spot long/short geometry shared by discretionary, paper, live, and backtest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.products import base_currency as spot_base_currency
from thytrader.trading.models import OrderSide, PositionSide

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.models import Candle

PROTECTIVE_STOP_LIMIT_BUFFER = Decimal("0.05")
"""Limit offset of a live stop-only protective order, as a fraction of its stop price.

Coinbase fills the stop leg of a ``trigger_bracket_gtc`` as a stop-limit priced 5% through
the trigger; the stop-only order uses the same offset so its gap risk matches (ADR 0090).
"""


class EntrySkipReason(StrEnum):
    """Why a matched entry signal rested no order: shared by backtest, paper, and live.

    Values are lower-case for backtest diagnostics; the decision journal records the
    upper-cased value as its ``reason_code``.
    """

    ENTRY_PRICE_NOT_POSITIVE = "entry_price_not_positive"
    STOP_DISTANCE_NOT_POSITIVE = "stop_distance_not_positive"
    STOP_NOT_POSITIVE = "stop_not_positive"
    TARGET_NOT_POSITIVE = "target_not_positive"
    STOP_WITHIN_PRICE_INCREMENT = "stop_within_price_increment"
    TARGET_WITHIN_PRICE_INCREMENT = "target_within_price_increment"
    NET_TARGET_BELOW_MINIMUM = "net_target_below_minimum"
    ECONOMICS_FEE_UNAVAILABLE = "economics_fee_unavailable"
    SIZING_CASH_UNAVAILABLE = "sizing_cash_unavailable"
    NO_OPEN_POSITION = "no_open_position"
    INSUFFICIENT_CASH = "insufficient_cash"
    NOTIONAL_BELOW_MINIMUM = "notional_below_minimum"
    QUANTITY_BELOW_VENUE_MINIMUM = "quantity_below_venue_minimum"
    NOTIONAL_BELOW_VENUE_MINIMUM = "notional_below_venue_minimum"


_GEOMETRY_SKIPS = frozenset(
    {
        EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE,
        EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE,
        EntrySkipReason.STOP_NOT_POSITIVE,
        EntrySkipReason.TARGET_NOT_POSITIVE,
        EntrySkipReason.STOP_WITHIN_PRICE_INCREMENT,
        EntrySkipReason.TARGET_WITHIN_PRICE_INCREMENT,
        EntrySkipReason.NET_TARGET_BELOW_MINIMUM,
        EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE,
    }
)


def entry_skip_category(reason: EntrySkipReason) -> Literal["geometry", "sizing"]:
    """Group a skip as stop/target geometry or as cash and venue-minimum sizing."""
    return "geometry" if reason in _GEOMETRY_SKIPS else "sizing"


@dataclass(frozen=True, slots=True)
class EntryLevels:
    """Legal stop and optional take-profit for one entry price (target None = no TP)."""

    stop_price: Decimal
    target_price: Decimal | None


def _snap(value: Decimal, increment: Decimal | None, *, rounding: str) -> Decimal:
    """Quantize onto a venue increment, or keep research-exact prices when there is none."""
    if increment is None:
        return value
    if increment <= 0:
        raise ValueError("venue increment must be positive")
    return (value / increment).quantize(Decimal("1"), rounding=rounding) * increment


def entry_levels(
    *,
    side: PositionSide,
    entry_price: Decimal,
    stop_distance: Decimal,
    reward_multiple: Decimal | None,
    price_increment: Decimal | None = None,
) -> EntryLevels | EntrySkipReason:
    """Return stop/target for one entry, or the explicit reason the geometry is illegal.

    Backtests pass no increment (exact research prices); paper and live snap the stop
    away from entry and the target toward it, exactly as before. ``reward_multiple`` None
    means the strategy declares no take-profit, so only the stop is checked. A short
    whose target would be at or below zero is ``target_not_positive``, never a silent skip.
    """
    if entry_price <= 0:
        return EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE
    if stop_distance <= 0:
        return EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE
    if side is PositionSide.LONG:
        stop = _snap(entry_price - stop_distance, price_increment, rounding=ROUND_DOWN)
        if stop <= 0:
            return EntrySkipReason.STOP_NOT_POSITIVE
        if reward_multiple is None:
            return EntryLevels(stop_price=stop, target_price=None)
        target = _snap(
            entry_price + stop_distance * reward_multiple, price_increment, rounding=ROUND_HALF_UP
        )
        if target <= entry_price:
            return EntrySkipReason.TARGET_WITHIN_PRICE_INCREMENT
        return EntryLevels(stop_price=stop, target_price=target)
    stop = _snap(entry_price + stop_distance, price_increment, rounding=ROUND_HALF_UP)
    target_price: Decimal | None = None
    if reward_multiple is not None:
        target_price = _snap(
            entry_price - stop_distance * reward_multiple, price_increment, rounding=ROUND_DOWN
        )
        if target_price <= 0:
            return EntrySkipReason.TARGET_NOT_POSITIVE
    if stop <= entry_price:
        return EntrySkipReason.STOP_WITHIN_PRICE_INCREMENT
    return EntryLevels(stop_price=stop, target_price=target_price)


def protective_stop_limit_price(
    *, cover_side: OrderSide, stop_price: Decimal, price_increment: Decimal
) -> Decimal:
    """Return the limit of a live stop-only exit: 5% through the stop, snapped marketable.

    A sell (long cover) limits below the stop, rounded down; a buy (short cover) limits
    above it, rounded up, so snapping never moves the limit back toward the trigger.
    """
    if cover_side is OrderSide.SELL:
        raw = stop_price * (Decimal("1") - PROTECTIVE_STOP_LIMIT_BUFFER)
        return _snap(raw, price_increment, rounding=ROUND_DOWN)
    raw = stop_price * (Decimal("1") + PROTECTIVE_STOP_LIMIT_BUFFER)
    return _snap(raw, price_increment, rounding=ROUND_UP)


def parse_position_side(value: str) -> PositionSide:
    """Parse ``long`` or ``short``."""
    try:
        return PositionSide(value)
    except ValueError as error:
        raise ValueError("side must be long or short.") from error


def entry_order_side(side: PositionSide) -> OrderSide:
    """Return the spot order side that opens one position."""
    return OrderSide.BUY if side is PositionSide.LONG else OrderSide.SELL


def exit_order_side(side: PositionSide) -> OrderSide:
    """Return the spot order side that covers one position."""
    return OrderSide.SELL if side is PositionSide.LONG else OrderSide.BUY


def bracket_is_valid(
    *,
    side: PositionSide,
    entry: Decimal,
    stop: Decimal,
    take_profit: Decimal,
) -> bool:
    """Return whether stop, entry, and take-profit order correctly for one side."""
    if entry <= 0 or stop <= 0 or take_profit <= 0:
        return False
    if side is PositionSide.LONG:
        return stop < entry < take_profit
    return take_profit < entry < stop


def bracket_error_detail(side: PositionSide) -> str:
    """Return the fail-closed message for an illegal SL/TP order."""
    if side is PositionSide.LONG:
        return "Longs require stop_price < entry < take_profit_price."
    return "Shorts require take_profit_price < entry < stop_price."


def paper_stop_hit(*, side: PositionSide, candle: Candle, stop_price: Decimal) -> bool:
    """Return whether a closed bar trades through the working stop."""
    if side is PositionSide.LONG:
        return candle.low <= stop_price
    return candle.high >= stop_price


def paper_stop_fill_price(*, side: PositionSide, candle: Candle, stop_price: Decimal) -> Decimal:
    """Return the conservative marketable stop fill price on a closed bar."""
    if side is PositionSide.LONG:
        return min(candle.open, stop_price)
    return max(candle.open, stop_price)


def base_currency(product_id: str) -> str:
    """Return the base currency from one BASE-USD or BASE-USDC product id."""
    return spot_base_currency(product_id)


def entry_bar_bucket(fill_time: datetime, timeframe: str) -> datetime:
    """Return the ``starts_at`` of the bar that contains ``fill_time`` on one timeframe."""
    interval = parse_candle_interval(timeframe)
    instant = fill_time.astimezone(UTC).replace(second=0, microsecond=0)
    closed_end = interval.align_closed_end(instant + interval.duration)
    return closed_end - interval.duration


_SKIP_DETAILS: dict[EntrySkipReason, str] = {
    EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE: (
        "the economic guard requires a current live fee profile"
    ),
    EntrySkipReason.NET_TARGET_BELOW_MINIMUM: (
        "the maker target after both fees does not meet the declared net return hurdle, "
        "or no target is declared"
    ),
    EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE: "the entry price is not positive",
    EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE: (
        "the ATR stop distance is not positive (ATR is zero, or an add is at or past the stop)"
    ),
    EntrySkipReason.STOP_NOT_POSITIVE: (
        "the long's initial stop would be at or below zero (stop distance at or above price)"
    ),
    EntrySkipReason.TARGET_NOT_POSITIVE: (
        "the short's take-profit would be at or below zero (reward multiple times stop "
        "distance at or above price); lower the multiples or use take_profit kind none"
    ),
    EntrySkipReason.STOP_WITHIN_PRICE_INCREMENT: (
        "the stop rounds onto the entry price at the venue price increment"
    ),
    EntrySkipReason.TARGET_WITHIN_PRICE_INCREMENT: (
        "the take-profit rounds onto the entry price at the venue price increment"
    ),
    EntrySkipReason.SIZING_CASH_UNAVAILABLE: "sizing cash (venue quote balance) is unknown",
    EntrySkipReason.NO_OPEN_POSITION: "there is no open position to add to",
    EntrySkipReason.INSUFFICIENT_CASH: "available cash cannot fund the order",
    EntrySkipReason.NOTIONAL_BELOW_MINIMUM: (
        "the risk-sized notional is below the strategy's min_quote_notional"
    ),
    EntrySkipReason.QUANTITY_BELOW_VENUE_MINIMUM: (
        "the quantized quantity is below the product's base minimum size"
    ),
    EntrySkipReason.NOTIONAL_BELOW_VENUE_MINIMUM: (
        "the quantized notional is below the product's quote minimum size"
    ),
}


def entry_skip_detail(reason: EntrySkipReason) -> str:
    """Return one operator-facing sentence fragment explaining a skip reason."""
    return _SKIP_DETAILS[reason]
