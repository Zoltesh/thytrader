"""Fills of the backtest kernel: opening, scaling in, and closing with cash transitions.

Maker entry and pyramid-add fills (refused when shared cash cannot fund them), the
covering exit fill with complete-trade evidence, and spread-stress fill evidence.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypedDict

from thytrader.backtest.kernel_state import (
    ExitReason,
    _Book,
    _Costs,
    _PendingEntry,
    _Position,
    _Tally,
)
from thytrader.backtest.models import BacktestExitFill, BacktestFill, BacktestTrade
from thytrader.decimal_text import canonical_decimal

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from thytrader.backtest.broker import FillQuote
    from thytrader.market_data.models import Candle


class _FillEvidence(TypedDict, total=False):
    """Spread-stress fill fields, omitted from maker fills and zero-spread runs."""

    reference_price: str
    executable_side: Literal["ask", "bid"]
    spread_cost: str


def _fill_resting_entry(
    book: _Book,
    pending: _PendingEntry,
    candle: Candle,
    *,
    offset: int,
    costs: _Costs,
    cash: Decimal,
    tally: _Tally,
) -> Decimal:
    """Open or scale into the book; a fill the shared cash can no longer fund is refused."""
    position = book.position
    if position is not None:
        scaled, cash = _scale_in(pending, position=position, cash=cash, costs=costs)
        # ``_scale_in`` hands back the unchanged position when cash cannot fund the add.
        if scaled is position:
            tally.entries_refused_at_fill += 1
        else:
            tally.entries_filled += 1
        book.position = scaled
        return cash
    book.position, cash = _open_position(
        pending, candle, cash=cash, entry_bar_index=offset, costs=costs
    )
    if book.position is None:
        tally.entries_refused_at_fill += 1
    else:
        tally.entries_filled += 1
    return cash


def _open_position(
    pending: _PendingEntry,
    candle: Candle,
    *,
    cash: Decimal,
    entry_bar_index: int,
    costs: _Costs,
) -> tuple[_Position | None, Decimal]:
    """Fill the resting limit at the posted price with the published maker fee."""
    short = pending.side == "short"
    quote = costs.fill_model.maker(pending.limit_price)
    notional = pending.quantity * quote.price
    fee = notional * costs.maker_fee_rate
    if not short and notional + fee > cash:
        return None, cash
    entry = BacktestFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(quote.price),
        quantity=canonical_decimal(pending.quantity),
        notional=canonical_decimal(notional),
        fee=canonical_decimal(fee),
        fee_rate=canonical_decimal(costs.maker_fee_rate),
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    return (
        _Position(
            entry=entry,
            stop_price=pending.stop_price,
            target_price=pending.target_price,
            entered_bar_index=entry_bar_index,
            side=pending.side,
        ),
        next_cash,
    )


def _scale_in(
    pending: _PendingEntry,
    *,
    position: _Position,
    cash: Decimal,
    costs: _Costs,
) -> tuple[_Position, Decimal]:
    """VWAP a same-side maker add onto the open book without worsening stop or target."""
    short = pending.side == "short"
    quote = costs.fill_model.maker(pending.limit_price)
    add_quantity = pending.quantity
    notional = add_quantity * quote.price
    fee = notional * costs.maker_fee_rate
    if not short and notional + fee > cash:
        return position, cash
    quantity = Decimal(position.entry.quantity) + add_quantity
    entry_price = (
        Decimal(position.entry.price) * Decimal(position.entry.quantity)
        + quote.price * add_quantity
    ) / quantity
    entry = BacktestFill(
        candle_starts_at=position.entry.candle_starts_at,
        price=canonical_decimal(entry_price),
        quantity=canonical_decimal(quantity),
        notional=canonical_decimal(quantity * entry_price),
        fee=canonical_decimal(Decimal(position.entry.fee) + fee),
        fee_rate=position.entry.fee_rate,
    )
    next_cash = cash + notional - fee if short else cash - notional - fee
    return replace(position, entry=entry, add_count=position.add_count + 1), next_cash


def _close_position(
    position: _Position,
    candle: Candle,
    *,
    cash: Decimal,
    quote: FillQuote,
    reason: ExitReason,
    fee_rate: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade, Decimal]:
    """Apply one covering fill, fee, cash transition, and exact complete-trade evidence."""
    short = position.side == "short"
    quantity = Decimal(position.entry.quantity)
    exit_notional = quantity * quote.price
    exit_fee = exit_notional * fee_rate
    exit_fill = BacktestExitFill(
        candle_starts_at=candle.starts_at,
        price=canonical_decimal(quote.price),
        quantity=position.entry.quantity,
        notional=canonical_decimal(exit_notional),
        fee=canonical_decimal(exit_fee),
        fee_rate=canonical_decimal(fee_rate),
        reason=reason,
        **_fill_evidence(quote),
    )
    entry_notional = Decimal(position.entry.notional)
    entry_fee = Decimal(position.entry.fee)
    if short:
        net_pnl = entry_notional - entry_fee - exit_notional - exit_fee
        gross_pnl = entry_notional - exit_notional
        next_cash = cash - exit_notional - exit_fee
    else:
        net_pnl = exit_notional - exit_fee - entry_notional - entry_fee
        gross_pnl = exit_notional - entry_notional
        next_cash = cash + exit_notional - exit_fee
    return (
        BacktestTrade(
            entry=position.entry,
            exit=exit_fill,
            gross_pnl=canonical_decimal(gross_pnl),
            net_pnl=canonical_decimal(net_pnl),
            holding_bars=_holding_bars(
                position.entry.candle_starts_at, candle.starts_at, bar_duration
            ),
        ),
        next_cash,
    )


def _fill_evidence(quote: FillQuote) -> _FillEvidence:
    """Record executable-side spread evidence only for spread-stressed taker fills."""
    if quote.executable_side is None:
        return {}
    return {
        "reference_price": canonical_decimal(quote.reference_price),
        "executable_side": quote.executable_side,
        "spread_cost": canonical_decimal(quote.spread_cost),
    }


def _holding_bars(entry: datetime, exit_: datetime, bar_duration: timedelta) -> int:
    """Return exact whole bars between modeled entry and exit boundaries."""
    return int((exit_ - entry) / bar_duration)
