"""Exits of the backtest kernel: stop, take-profit, trailing, signal, and time exits.

The stop wins a same-bar tie with the resting take-profit; the signal and time exits
sell at the close as a taker after the fill bar, and marketable exits are priced at
the executable side with slippage.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.backtest.kernel_atr import _trail_position
from thytrader.backtest.kernel_fills import _close_position
from thytrader.research.stress import maker_touched
from thytrader.research.trace import EntryConditionOutcome, SignalTraceRecord

if TYPE_CHECKING:
    from datetime import timedelta
    from decimal import Decimal

    from thytrader.backtest.broker import FillModel, FillQuote
    from thytrader.backtest.kernel_state import _Book, _Costs, _Position
    from thytrader.backtest.models import BacktestTrade
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


def _stop_out(
    book: _Book,
    candle: Candle,
    *,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade | None, Decimal]:
    """Exit a position whose take-profit is resting when the bar trades through the stop.

    The fill bar is handled by ``_manage_position`` (the target is not resting yet). This
    runs before the target match so a bar touching both exits is conservatively a stop.
    """
    position = book.position
    if position is None or not position.take_profit_resting:
        return None, cash
    if not _stop_hit(position, candle, costs.fill_model):
        return None, cash
    trade, cash = _close_position(
        position,
        candle,
        cash=cash,
        quote=_taker_exit_quote(position, _stop_reference(position, candle, costs), costs),
        reason="stop_loss",
        fee_rate=costs.taker_fee_rate,
        bar_duration=bar_duration,
    )
    book.position = None
    return trade, cash


def _match_take_profit(
    book: _Book,
    candle: Candle,
    *,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade | None, Decimal]:
    """Fill a resting take-profit at the target, as a maker, when a later bar touches it.

    A position without a target (``take_profit: {"kind": "none"}``) never fills here.
    """
    position = book.position
    if position is None or not position.take_profit_resting:
        return None, cash
    target = position.target_price
    if target is None:
        return None, cash
    hit = maker_touched(
        high=candle.high,
        low=candle.low,
        price=target,
        buy=position.side == "short",
        stress=costs.execution_stress,
    )
    if not hit:
        return None, cash
    trade, cash = _close_position(
        position,
        candle,
        cash=cash,
        quote=costs.fill_model.maker(target),
        reason="take_profit",
        fee_rate=costs.maker_fee_rate,
        bar_duration=bar_duration,
    )
    book.position = None
    return trade, cash


def _manage_position(
    book: _Book,
    candle: Candle,
    *,
    offset: int,
    strategy: StrategyDefinition,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade | None, Decimal]:
    """Check the entering stop, ratchet the trail, signal- or time-exit at close, then rest TP.

    The signal exit (ADR 0093) is never evaluated on the fill bar, runs only after the stop
    and the resting take-profit had their chance on this bar (the protective stop wins a
    same-bar tie), and precedes the time exit; both sell at this bar's close as a taker.
    """
    position = book.position
    if position is None:
        return None, cash
    if _stop_hit(position, candle, costs.fill_model):
        trade, cash = _close_position(
            position,
            candle,
            cash=cash,
            quote=_taker_exit_quote(position, _stop_reference(position, candle, costs), costs),
            reason="stop_loss",
            fee_rate=costs.taker_fee_rate,
            bar_duration=bar_duration,
        )
        book.position = None
        return trade, cash
    is_fill_bar = offset == position.entered_bar_index
    record = book.records.get(candle.starts_at)
    position = _trail_position(
        position,
        candle,
        strategy=strategy,
        record=record,
        is_fill_bar=is_fill_bar,
    )
    if not is_fill_bar and _signal_exit_matched(record):
        trade, cash = _close_position(
            position,
            candle,
            cash=cash,
            quote=_taker_exit_quote(position, candle.close, costs),
            reason="signal",
            fee_rate=costs.taker_fee_rate,
            bar_duration=bar_duration,
        )
        book.position = None
        return trade, cash
    if offset - position.entered_bar_index >= strategy.exits.time_exit.max_bars_held:
        trade, cash = _close_position(
            position,
            candle,
            cash=cash,
            quote=_taker_exit_quote(position, candle.close, costs),
            reason="time_exit",
            fee_rate=costs.taker_fee_rate,
            bar_duration=bar_duration,
        )
        book.position = None
        return trade, cash
    book.position = replace(position, take_profit_resting=True)
    return None, cash


def _signal_exit_matched(record: SignalTraceRecord | None) -> bool:
    """Whether this bar's ``exits.signal_exit`` rule matched (never for documents without one)."""
    return record is not None and record.exit_condition is EntryConditionOutcome.MATCHED


def _stop_hit(position: _Position, candle: Candle, fill_model: FillModel) -> bool:
    """Return whether the bar's executable extreme trades through the working stop."""
    if position.side == "short":
        return fill_model.ask(candle.high) >= position.stop_price
    return fill_model.bid(candle.low) <= position.stop_price


def _stop_reference(position: _Position, candle: Candle, costs: _Costs) -> Decimal:
    """Return the raw reference price of a stop exit, gapping to the adverse open."""
    if position.side == "short":
        return max(candle.open, costs.fill_model.raw_for_ask(position.stop_price))
    return min(candle.open, costs.fill_model.raw_for_bid(position.stop_price))


def _taker_exit_quote(position: _Position, reference_price: Decimal, costs: _Costs) -> FillQuote:
    """Price one marketable closing leg: sell longs at bid, cover shorts at ask, with slippage."""
    if position.side == "short":
        return costs.fill_model.taker_buy(reference_price, costs.slippage_bps)
    return costs.fill_model.taker_sell(reference_price, costs.slippage_bps)
