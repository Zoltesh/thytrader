"""Closed candle-window loading and selection for the execution worker.

Decision, HTF, indicator-timeframe and reference windows, the due-bar selection
over them, and the reference gate of one decision bar. Nothing here pauses a book.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.evaluation.models import warmup_starts_at
from thytrader.evaluation.multi_timeframe import closed_bar_required_coverage, ltf_close
from thytrader.execution.candle_wait import newest_bar_settling
from thytrader.execution.references import ReferenceGate, reference_gate
from thytrader.execution_worker.ports import _logger
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    extra_indicator_timeframes,
    reference_data_requirements,
    signal_exit_condition,
    strategy_indicator_operands,
)
from thytrader.trading.geometry import entry_bar_bucket

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle, CandleRangeReport, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition


async def _signal_exit_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    product_id: str | None = None,
) -> tuple[tuple[Candle, ...], dict[str, tuple[Candle, ...]]]:
    """Best-effort HTF and extra-TF windows a stopped book's exit rule reads (ADR 0093).

    Only a declared ``exits.signal_exit`` on a strategy with per-indicator extra
    timeframes needs them. A gapped window yields nothing: the exit rule then fails
    closed (no exit is invented) while the protective stop and time exit keep running.
    ``product_id`` selects a covered book; omitted, the primary instrument is used.
    """
    if signal_exit_condition(strategy.exits) is None or not extra_indicator_timeframes(strategy):
        return (), {}
    htf_candles = await _closed_htf_window(
        market_data, strategy, product_id=product_id, deploy_anchor=deploy_anchor
    )
    if htf_candles is None:
        return (), {}
    extra_candles = await _closed_indicator_timeframe_windows(
        market_data,
        strategy,
        htf_candles,
        product_id=product_id,
        deploy_anchor=deploy_anchor,
    )
    return htf_candles, extra_candles or {}


def new_closed_bars(
    candles: Sequence[Candle],
    *,
    last_evaluated_bar: datetime | None,
    expected_last_start: datetime,
    bar_duration: timedelta,
    allow_settling: bool = False,
    now: datetime | None = None,
) -> tuple[Candle, ...] | None:
    """Return due bars, an empty bounded publication wait, or None for unsafe gaps.

    ``allow_settling`` is for decision clocks only. The wait never hides an older
    cursor gap or advances evaluation; consumers must maintain inventory without entries.
    """
    if not candles or not _contiguous(candles, bar_duration):
        return None
    latest = candles[-1]
    if latest.starts_at != expected_last_start:
        if (
            allow_settling
            and newest_bar_settling(
                candles, expected_last_start=expected_last_start, bar_duration=bar_duration, now=now
            )
            and new_closed_bars(
                candles,
                last_evaluated_bar=last_evaluated_bar,
                expected_last_start=expected_last_start - bar_duration,
                bar_duration=bar_duration,
            )
            is not None
        ):
            return ()
        return None
    if last_evaluated_bar is None:
        return (latest,)
    due = tuple(candle for candle in candles if candle.starts_at > last_evaluated_bar)
    expected = last_evaluated_bar + bar_duration
    for candle in due:
        if candle.starts_at != expected:
            return None
        expected = candle.starts_at + bar_duration
    return due


def _contiguous(candles: Sequence[Candle], bar_duration: timedelta) -> bool:
    """Return whether candle starts are consecutive closed bars of one interval."""
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.starts_at - previous != bar_duration:
            return False
        previous = candle.starts_at
    return True


def htf_coverage_ready(
    candles: Sequence[Candle],
    *,
    expected_last_start: datetime,
    bar_duration: timedelta,
) -> bool:
    """True when HTF bars are contiguous and include the latest completed HTF bar."""
    if not candles or not _contiguous(candles, bar_duration):
        return False
    return candles[-1].starts_at == expected_last_start


def _shared_clock_union_warmup(
    strategy: StrategyDefinition, *, timeframe: str, warmup_bars: int, deploy_anchor: datetime
) -> int:
    """Cover the filter clock and every extra-timeframe indicator sharing it (ADR 0113).

    The shared window is fetched once at the union length. Each consumer's indicator rows
    are still selected from its own exact required bars, so the longer window does not move
    a seeded indicator value.
    """
    needed = _required_clock_warmup_bars(
        strategy, timeframe=timeframe, warmup_bars=warmup_bars, deploy_anchor=deploy_anchor
    )
    for clock, indicators in extra_indicator_timeframe_groups(strategy):
        if clock != timeframe:
            continue
        needed = max(
            needed,
            _required_clock_warmup_bars(
                strategy,
                timeframe=timeframe,
                warmup_bars=extra_indicator_timeframe_warmup(
                    indicators, operands=strategy_indicator_operands(strategy)
                ),
                deploy_anchor=deploy_anchor,
            ),
        )
    return needed


async def _closed_htf_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    product_id: str | None = None,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[Candle, ...] | None:
    """Fetch complete-only last-completed HTF bars, or None when gapped.

    The fetch covers the union of the HTF filter warmup and the warmup of every
    extra-timeframe indicator on the same clock, so reusing this window for that clock
    never shortens an indicator's history (ADR 0113).
    """
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        return ()
    _product, candles, expected_last = await _closed_window_for(
        market_data,
        product_id=product_id or strategy.instrument.product_id,
        timeframe=htf_filter.timeframe,
        warmup_bars=_shared_clock_union_warmup(
            strategy,
            timeframe=htf_filter.timeframe,
            warmup_bars=htf_filter.data_requirements.warmup_bars,
            deploy_anchor=deploy_anchor,
        ),
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )
    interval = parse_candle_interval(htf_filter.timeframe)
    if not htf_coverage_ready(
        candles,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
    ):
        return None
    return candles


async def _closed_indicator_timeframe_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    htf_candles: Sequence[Candle],
    *,
    product_id: str | None = None,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> dict[str, tuple[Candle, ...]] | None:
    """Fetch complete-only extra-TF bars; reuse a shared HTF clock only with union coverage."""
    windows: dict[str, tuple[Candle, ...]] = {}
    htf_timeframe = strategy.htf_filter.timeframe if strategy.htf_filter is not None else None
    covered_product = product_id or strategy.instrument.product_id
    for timeframe, indicators in extra_indicator_timeframe_groups(strategy):
        warmup = _required_clock_warmup_bars(
            strategy,
            timeframe=timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                indicators, operands=strategy_indicator_operands(strategy)
            ),
            deploy_anchor=deploy_anchor,
        )
        if timeframe == htf_timeframe:
            interval = parse_candle_interval(timeframe)
            expected = as_of_closed_start or (
                interval.align_closed_end(datetime.now(UTC)) - interval.duration
            )
            needed_start = warmup_starts_at(
                entry_bar_bucket(deploy_anchor, timeframe), warmup, timeframe
            )
            if (
                htf_candles
                and htf_candles[0].starts_at <= needed_start
                and htf_coverage_ready(
                    htf_candles, expected_last_start=expected, bar_duration=interval.duration
                )
            ):
                windows[timeframe] = tuple(htf_candles)
                continue
        _product, candles, expected_last = await _closed_window_for(
            market_data,
            product_id=covered_product,
            timeframe=timeframe,
            warmup_bars=warmup,
            deploy_anchor=deploy_anchor,
            as_of_closed_start=as_of_closed_start,
        )
        interval = parse_candle_interval(timeframe)
        if not htf_coverage_ready(
            candles,
            expected_last_start=expected_last,
            bar_duration=interval.duration,
        ):
            return None
        windows[timeframe] = candles
    return windows


def _required_clock_warmup_bars(
    strategy: StrategyDefinition,
    *,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
) -> int:
    """Cover the first decision's previous mapped bar without moving the deploy anchor.

    The first evaluated decision is the bar completed at deployment's decision-clock
    bucket. At a required-clock rollover, its previous close maps one bar earlier than
    the deployment bucket's normal warmup. Use the evaluator's coverage contract to
    extend that fixed window only when necessary; later cycles retain the same start.
    """
    decision_interval = parse_candle_interval(strategy.timeframe)
    first_decision = (
        entry_bar_bucket(deploy_anchor, strategy.timeframe) - decision_interval.duration
    )
    required_start, _required_end = closed_bar_required_coverage(
        evaluation_starts_at=first_decision,
        evaluation_ends_at=ltf_close(first_decision, strategy.timeframe),
        timeframe=timeframe,
        warmup_bars=warmup_bars,
    )
    interval = parse_candle_interval(timeframe)
    deploy_bucket = entry_bar_bucket(deploy_anchor, timeframe)
    return max(warmup_bars, (deploy_bucket - required_start) // interval.duration)


async def _closed_reference_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
) -> dict[str, tuple[Candle, ...]]:
    """Fetch each reference instrument's deploy-anchored closed bars (ADR 0096).

    Each window starts at the shared required-clock coverage boundary, so the first
    decision bar's current and previous mapped reference bars are both present at a
    clock rollover and no extra bar is fetched when the anchor sits mid-bucket
    (ADR 0113). Best effort: a reference whose fetch fails is returned empty, so the
    per-bar reference gate skips new entries with ``REFERENCE_DATA_MISSING`` while
    stops, targets, and exits keep running. Strategies without references fetch nothing.
    """
    windows: dict[str, tuple[Candle, ...]] = {}
    for requirement in reference_data_requirements(strategy):
        try:
            _product, candles, _expected = await _closed_window_for(
                market_data,
                product_id=requirement.product_id,
                timeframe=requirement.timeframe,
                warmup_bars=_required_clock_warmup_bars(
                    strategy,
                    timeframe=requirement.timeframe,
                    warmup_bars=requirement.warmup_bars,
                    deploy_anchor=deploy_anchor,
                ),
                deploy_anchor=deploy_anchor,
            )
        except WindowCacheWarmingError:
            # References gate only entries; warming must not suppress protective supervision.
            candles = ()
        except RuntimeError, ValueError, TypeError, OSError:
            _logger.warning(
                "reference_window_unavailable reference_id=%s product_id=%s timeframe=%s",
                requirement.reference_id,
                requirement.product_id,
                requirement.timeframe,
            )
            candles = ()
        windows[requirement.reference_id] = candles
    return windows


def _bar_reference_gate(
    strategy: StrategyDefinition,
    reference_candles: dict[str, tuple[Candle, ...]],
    candle: Candle,
    *,
    allow_new_entries: bool,
) -> ReferenceGate | None:
    """Gate one entry-eligible decision bar on reference readiness; None when it may enter."""
    if not allow_new_entries or not reference_data_requirements(strategy):
        return None
    return reference_gate(
        strategy,
        reference_candles,
        decision_close=ltf_close(candle.starts_at, strategy.timeframe),
    )


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch deploy-anchored warmup through the latest fully closed bar."""
    return await _closed_window_for(
        market_data,
        product_id=strategy.instrument.product_id,
        timeframe=strategy.timeframe,
        warmup_bars=strategy.data_requirements.warmup_bars,
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )


async def _closed_window_for(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch deploy-anchored warmup through one closed bar on an interval.

    The service's deploy-window cache (ADR 0113) segments settled history below the
    adapter's per-request bound and refetches the newest/unsettled tail with overlap.
    The start never slides: seeded indicators still consume their exact canonical
    deploy-prefix. Frozen history ignores provider corrections within this service
    generation; restart/eviction re-observes history from the same boundary. Budget
    exhaustion raises WindowCacheWarmingError, not an empty/gapped data verdict:
    consumers retry without changing lifecycle choices or evaluating a partial seed.
    Retained history and full indicator compute still grow with deployment lifetime.

    Coinbase returns no candle for an interval without trades. A bar missing between two
    real candles, and still missing on one re-fetch, is a confirmed no-trade interval and
    becomes a flat zero-volume bar, exactly as research datasets publish it (ADR 0095).
    A missing newest bar is never filled; decision clocks may wait for ADR 0104's
    bounded publication window before pausing. Required filter clocks remain fail-closed.
    """
    now = datetime.now(UTC)
    interval = parse_candle_interval(timeframe)
    last_closed_end = interval.align_closed_end(now)
    last_closed_start = (
        as_of_closed_start
        if as_of_closed_start is not None
        else last_closed_end - interval.duration
    )
    deploy_anchor_bar = entry_bar_bucket(deploy_anchor, timeframe)
    starts_at = warmup_starts_at(deploy_anchor_bar, warmup_bars, timeframe)
    preview = await market_data.get_preview(product_id, interval)

    async def fetch_range(starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Fetch one bounded segment at this cycle's immutable observation instant."""
        return await market_data.get_range(product_id, interval, starts_at, ends_at, now)

    candles = await market_data.window_cache.closed_window(
        fetch_range,
        product_id=product_id,
        interval=interval,
        starts_at=starts_at,
        last_closed_start=last_closed_start,
        now=now,
    )
    return preview.product, candles, last_closed_start
