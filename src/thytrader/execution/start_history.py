"""Refuse a start whose markets are younger than the strategy's warmup (ADR 0140).

A book fetches every clock's window from the venue on demand, starting a fixed number of
bars before its creation (``clock_windows``). When a market was listed after that start, the
venue simply has no earlier candles: the indicators would be seeded at the listing and
trade before they converge. A start therefore checks, with one bounded range request per
series (two for a thin market), that each series traded at or before its window start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.clock_windows import ClockWindow, deployment_clock_windows
from thytrader.market_data.models import (
    HISTORICAL_REQUEST_MAX_CANDLES,
    CandleInterval,
    parse_candle_interval,
)
from thytrader.market_data.watch_coverage import utc_day_floor
from thytrader.trading.models import ExecutionConflictError

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition

HISTORY_TOO_SHORT = "HISTORY_TOO_SHORT"
"""Reason-code prefix of a start refused for missing venue history."""

HISTORY_UNCONFIRMED = "HISTORY_UNCONFIRMED"
"""Reason-code prefix of a start refused because the venue history could not be read."""


async def require_start_history(
    market_data: MarketDataService, strategy: StrategyDefinition, *, now: datetime
) -> None:
    """Raise unless every window of a book starting ``now`` has venue history at its start."""
    try:
        shortfalls = await start_history_shortfalls(market_data, strategy, now=now)
    except ExecutionConflictError as error:
        raise ExecutionConflictError(f"{error} Nothing was started; retry the start.") from error
    if shortfalls:
        raise ExecutionConflictError(history_shortfall_message(shortfalls))


async def start_history_shortfalls(
    market_data: MarketDataService, strategy: StrategyDefinition, *, now: datetime
) -> tuple[ClockWindow, ...]:
    """The windows of a book starting ``now`` whose series do not reach back to their start.

    A failed venue read raises ``ExecutionConflictError`` (``HISTORY_UNCONFIRMED``): the
    start is refused rather than assumed covered.
    """
    windows = _earliest_per_series(deployment_clock_windows(strategy, deploy_anchor=now))
    return tuple(
        [window for window in windows if not await _reaches_start(market_data, window, now=now)]
    )


def history_shortfall_message(shortfalls: tuple[ClockWindow, ...], *, refused: bool = True) -> str:
    """Name each short series and what happens: refused, or started and waiting (ADR 0142)."""
    series = "; ".join(
        f"{item.role} {item.product_id} {item.timeframe} needs {item.warmup_bars} bars from "
        f"{item.starts_at:%Y-%m-%d %H:%M} UTC"
        for item in shortfalls
    )
    outcome = (
        "Nothing was started. Use shorter indicators or a market with longer history."
        if refused
        else "Started anyway: the bot skips entries until each window holds its warmup. Use "
        "shorter indicators, or set the risk policy's require_market_history to refuse this."
    )
    return (
        f"{HISTORY_TOO_SHORT}: the venue has no candles that early for {series}. The market is "
        f"younger than the strategy's warmup (ADR 0140). {outcome}"
    )


async def start_history_warning(
    market_data: MarketDataService, strategy: StrategyDefinition, *, now: datetime
) -> str | None:
    """The warning a start carries when its history is short or unreadable (ADR 0142)."""
    try:
        shortfalls = await start_history_shortfalls(market_data, strategy, now=now)
    except ExecutionConflictError as error:
        return (
            f"{error} Started anyway; the bot skips entries until its history is confirmed and "
            "long enough (the risk policy's require_market_history refuses this)."
        )
    return history_shortfall_message(shortfalls, refused=False) if shortfalls else None


def _earliest_per_series(windows: tuple[ClockWindow, ...]) -> tuple[ClockWindow, ...]:
    """Keep the earliest-starting window of each product and timeframe."""
    earliest: dict[tuple[str, str], ClockWindow] = {}
    for window in windows:
        key = (window.product_id, window.timeframe)
        current = earliest.get(key)
        if current is None or window.starts_at < current.starts_at:
            earliest[key] = window
    return tuple(earliest.values())


async def _reaches_start(
    market_data: MarketDataService, window: ClockWindow, *, now: datetime
) -> bool:
    """Whether the series traded at or before the window start.

    The window's own clock is read first: its first bar and the bars just before it, so a
    market with no trade on that bar still counts as listed. A thin market can go hundreds of
    short bars without a trade, so when that read is empty, daily bars over the previous year
    are read, counting only a day that closed by the window start.
    """
    interval = parse_candle_interval(window.timeframe)
    if await _traded_by(market_data, window, interval, through=window.starts_at, now=now):
        return True
    day = CandleInterval.ONE_DAY
    if interval is day:
        return False
    last_closed_day = utc_day_floor(window.starts_at) - day.duration
    return await _traded_by(market_data, window, day, through=last_closed_day, now=now)


async def _traded_by(
    market_data: MarketDataService,
    window: ClockWindow,
    interval: CandleInterval,
    *,
    through: datetime,
    now: datetime,
) -> bool:
    """Whether one bounded read has a bar of ``interval`` starting at or before ``through``."""
    ends_at = through + interval.duration
    starts_at = ends_at - interval.duration * HISTORICAL_REQUEST_MAX_CANDLES
    try:
        report = await market_data.get_range(window.product_id, interval, starts_at, ends_at, now)
    except (RuntimeError, ValueError, TypeError, OSError) as error:
        raise ExecutionConflictError(
            f"{HISTORY_UNCONFIRMED}: could not read {window.product_id} {interval.value} "
            f"history from the venue ({type(error).__name__}), so its warmup cannot be "
            "confirmed."
        ) from error
    return any(candle.starts_at <= through for candle in report.quality.candles)
