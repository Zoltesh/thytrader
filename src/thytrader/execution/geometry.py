"""Spot long/short geometry shared by discretionary, paper, live, and backtest."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from thytrader.execution.models import OrderSide, PositionSide
from thytrader.market_data.models import parse_candle_interval

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from thytrader.market_data.models import Candle


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
    """Return the BASE from a BASE-USD product id."""
    return product_id.split("-", 1)[0]


def entry_bar_bucket(fill_time: datetime, timeframe: str) -> datetime:
    """Return the ``starts_at`` of the bar that contains ``fill_time`` on one timeframe."""
    interval = parse_candle_interval(timeframe)
    instant = fill_time.astimezone(UTC).replace(second=0, microsecond=0)
    closed_end = interval.align_closed_end(instant + interval.duration)
    return closed_end - interval.duration
