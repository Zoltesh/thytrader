"""ATR-multiple trailing-stop ratchet shared by backtest, paper, and live."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from thytrader.execution.sizing import quantize_to_increment


@dataclass(frozen=True, slots=True)
class TrailingStopState:
    """Working long stop and highest high used by the next ratchet."""

    stop_price: Decimal
    trail_extreme: Decimal


def ratcheted_long_stop(
    *,
    current_stop: Decimal,
    trail_extreme: Decimal | None,
    bar_high: Decimal,
    atr: Decimal | None,
    multiple: Decimal,
    price_increment: Decimal | None,
    ratchet: bool,
) -> TrailingStopState:
    """Advance the highest high; raise the stop only on later bars with a defined ATR.

    The fill bar records ``trail_extreme`` without changing the initial stop. Missing
    ATR leaves the stop unchanged (never zero). The working stop never decreases.
    """
    extreme = bar_high if trail_extreme is None else max(trail_extreme, bar_high)
    if not ratchet or atr is None or atr <= 0 or multiple <= 0:
        return TrailingStopState(stop_price=current_stop, trail_extreme=extreme)
    candidate = extreme - (atr * multiple)
    if price_increment is not None:
        candidate = quantize_to_increment(candidate, price_increment)
    if candidate <= 0:
        return TrailingStopState(stop_price=current_stop, trail_extreme=extreme)
    return TrailingStopState(stop_price=max(current_stop, candidate), trail_extreme=extreme)
