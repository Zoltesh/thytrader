"""Confirmed no-trade bars: flat zero-volume candles for intervals without trades (ADR 0095).

Coinbase returns no candle for an interval in which nothing traded. A sparse market
therefore has interior holes that are not missing data: the price simply did not move.
Ingest and the paper/live window loader fill such confirmed intervals with one flat bar
each (``open = high = low = close`` = the previous close, ``volume = 0``), so backtests,
paper, and live evaluate the same contiguous series. A bar before the first real candle
is never synthesized: without a previous close there is no honest price for it.

Real provider candles always carry positive volume, so ``volume == 0`` identifies a
synthetic no-trade bar without a separate flag; dataset manifests count them.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.market_data.models import Candle

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime, timedelta

    from thytrader.market_data.models import CandleInterval

NO_TRADE_VOLUME = Decimal(0)


def no_trade_bar(starts_at: datetime, previous_close: Decimal) -> Candle:
    """Return the flat zero-volume bar for one interval that had no trades."""
    return Candle(
        starts_at=starts_at,
        open=previous_close,
        high=previous_close,
        low=previous_close,
        close=previous_close,
        volume=NO_TRADE_VOLUME,
    )


def is_no_trade_bar(candle: Candle) -> bool:
    """True when the bar records no traded volume (a synthetic no-trade bar)."""
    return candle.volume == NO_TRADE_VOLUME


def count_no_trade_bars(candles: Iterable[Candle]) -> int:
    """Count synthetic no-trade bars in a candle sequence."""
    return sum(1 for candle in candles if is_no_trade_bar(candle))


def has_interior_gaps(candles: Sequence[Candle], interval: CandleInterval) -> bool:
    """True when some interval between two consecutive candles has no candle."""
    return any(
        later.starts_at - earlier.starts_at != interval.duration
        for earlier, later in pairwise(candles)
    )


def merge_confirmed_candles(
    first: Sequence[Candle], confirmation: Sequence[Candle]
) -> tuple[Candle, ...]:
    """Union two responses for one window, so a bar is missing only when both omit it.

    The confirmation's candle wins where both carry a bar.
    """
    by_start = {candle.starts_at: candle for candle in first}
    by_start.update({candle.starts_at: candle for candle in confirmation})
    return tuple(by_start[start] for start in sorted(by_start))


def fill_no_trade_gaps(
    candles: Sequence[Candle],
    interval: CandleInterval,
    *,
    through: datetime | None = None,
) -> tuple[Candle, ...]:
    """Insert flat bars for every interval missing between consecutive candles.

    ``candles`` must be strictly increasing and on the interval grid; callers pass only
    intervals they have confirmed as no-trade (settled and re-fetched). With ``through``,
    bars after the last candle up to that exclusive end are filled too. Leading gaps are
    left alone, so the result always starts at a real candle.
    """
    if not candles:
        return ()
    step = interval.duration
    filled: list[Candle] = [candles[0]]
    for candle in candles[1:]:
        _append_flat_until(filled, candle.starts_at, step)
        filled.append(candle)
    if through is not None:
        _append_flat_until(filled, through, step)
    return tuple(filled)


def _append_flat_until(filled: list[Candle], end: datetime, step: timedelta) -> None:
    """Append flat bars at the last close until the next bar would start at ``end``."""
    previous = filled[-1]
    cursor = previous.starts_at + step
    while cursor < end:
        filled.append(no_trade_bar(cursor, previous.close))
        cursor = cursor + step
