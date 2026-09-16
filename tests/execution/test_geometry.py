"""Spot long/short geometry used by discretionary, paper, live, and backtest."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from thytrader.execution.geometry import (
    bracket_is_valid,
    entry_order_side,
    exit_order_side,
    paper_stop_hit,
    parse_position_side,
)
from thytrader.execution.models import OrderSide, PositionSide
from thytrader.market_data.models import Candle


def test_parse_and_order_sides() -> None:
    """Longs buy to open; shorts sell to open."""
    assert parse_position_side("long") is PositionSide.LONG
    assert parse_position_side("short") is PositionSide.SHORT
    assert entry_order_side(PositionSide.LONG) is OrderSide.BUY
    assert entry_order_side(PositionSide.SHORT) is OrderSide.SELL
    assert exit_order_side(PositionSide.LONG) is OrderSide.SELL
    assert exit_order_side(PositionSide.SHORT) is OrderSide.BUY


def test_bracket_order_is_side_correct() -> None:
    """Longs require stop below entry below target; shorts invert that order."""
    entry = Decimal("100")
    assert bracket_is_valid(
        side=PositionSide.LONG, entry=entry, stop=Decimal("90"), take_profit=Decimal("120")
    )
    assert not bracket_is_valid(
        side=PositionSide.LONG, entry=entry, stop=Decimal("110"), take_profit=Decimal("80")
    )
    assert bracket_is_valid(
        side=PositionSide.SHORT, entry=entry, stop=Decimal("110"), take_profit=Decimal("80")
    )
    assert not bracket_is_valid(
        side=PositionSide.SHORT, entry=entry, stop=Decimal("90"), take_profit=Decimal("120")
    )


def test_paper_stop_uses_low_for_longs_and_high_for_shorts() -> None:
    """A closed bar through the stop is conservative on each side."""
    candle = Candle(
        starts_at=datetime(2026, 9, 16, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("111"),
        low=Decimal("89"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    assert paper_stop_hit(side=PositionSide.LONG, candle=candle, stop_price=Decimal("90"))
    assert not paper_stop_hit(side=PositionSide.LONG, candle=candle, stop_price=Decimal("80"))
    assert paper_stop_hit(side=PositionSide.SHORT, candle=candle, stop_price=Decimal("110"))
    assert not paper_stop_hit(side=PositionSide.SHORT, candle=candle, stop_price=Decimal("120"))
