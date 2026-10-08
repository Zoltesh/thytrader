"""ATR in the backtest kernel: signal-bar ATR values and the ATR trailing-stop ratchet.

Entry sizing reads the entry signal's exact ATR; position management ratchets the
trailing stop with the shared paper/live ``ratcheted_*_stop`` rules.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.kernel_state import BacktestSimulationError, _Position
from thytrader.strategies.models import StrategyDefinition, atr_trailing_stop
from thytrader.trading.trailing import ratcheted_long_stop, ratcheted_short_stop

if TYPE_CHECKING:
    from thytrader.evaluation.trace import SignalTraceRecord
    from thytrader.market_data.models import Candle


def _trail_position(
    position: _Position,
    candle: Candle,
    *,
    strategy: StrategyDefinition,
    record: SignalTraceRecord | None,
    is_fill_bar: bool,
) -> _Position:
    """Advance an ATR trailing stop after the fill bar; no-op when disabled."""
    policy = atr_trailing_stop(strategy.exits)
    if policy is None:
        return position
    atr = _optional_indicator_value(record, policy.atr_indicator)
    if position.side == "short":
        state = ratcheted_short_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_low=candle.low,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=None,
            ratchet=not is_fill_bar,
        )
    else:
        state = ratcheted_long_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_high=candle.high,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=None,
            ratchet=not is_fill_bar,
        )
    if state.stop_price == position.stop_price and state.trail_extreme == position.trail_extreme:
        return position
    return replace(position, stop_price=state.stop_price, trail_extreme=state.trail_extreme)


def _indicator_value(record: SignalTraceRecord, indicator_id: str) -> Decimal:
    """Load the exact ATR value that was available when the entry signal closed."""
    value = _optional_indicator_value(record, indicator_id)
    if value is None:
        raise BacktestSimulationError("Backtest entry signal lacks its required ATR value.")
    return value


def _optional_indicator_value(
    record: SignalTraceRecord | None, indicator_id: str
) -> Decimal | None:
    """Return a named indicator value when present, otherwise None."""
    if record is None:
        return None
    for value in record.indicator_values:
        if value.indicator_id == indicator_id and value.value is not None:
            return Decimal(value.value)
    return None
