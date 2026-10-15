"""Closed candle-window loading and selection for the execution worker.

Decision, HTF, indicator-timeframe and reference windows, the due-bar selection
over them, and the reference gate of one decision bar. Nothing here pauses a book.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.evaluation.models import warmup_starts_at
from thytrader.evaluation.multi_timeframe import ltf_close
from thytrader.execution.candle_wait import newest_bar_settling
from thytrader.execution.clock_windows import (
    deployment_clock_windows,
    required_clock_warmup_bars,
    shared_clock_union_warmup,
    windows_hold_warmup,
)
from thytrader.execution.closed_windows import _closed_window_for
from thytrader.execution.decisions import DecisionSkipReason, DecisionWindow
from thytrader.execution.references import ReferenceGate, reference_gate
from thytrader.execution_worker.ports import _logger
from thytrader.market_data.models import as_dataset_timeframe, parse_candle_interval
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
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle
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
        warmup_bars=shared_clock_union_warmup(
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
        warmup = required_clock_warmup_bars(
            strategy,
            timeframe=timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                indicators,
                operands=strategy_indicator_operands(strategy),
                policy=strategy.data_requirements.warmup_policy,
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
                warmup_bars=required_clock_warmup_bars(
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


SHORT_HISTORY_DETAIL = (
    "the market's history is shorter than the strategy's warmup, so its indicators have not "
    "converged (ADR 0140); no entry until it has accumulated that history"
)


def _bar_seed_gate(
    strategy: StrategyDefinition,
    window: Sequence[Candle],
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
    *,
    allow_new_entries: bool,
) -> ReferenceGate | None:
    """Gate one entry-eligible decision bar whose windows are short of warmup; else None."""
    if not allow_new_entries or windows_hold_warmup(
        strategy, window, htf_candles, indicator_timeframe_candles
    ):
        return None
    return ReferenceGate(reason=DecisionSkipReason.WARMUP, detail=SHORT_HISTORY_DETAIL)


def _covered_seed_gate(
    strategy: StrategyDefinition,
    windows: Mapping[str, Sequence[Candle]],
    htf_by_product: Mapping[str, Sequence[Candle]],
    extra_by_product: Mapping[str, Mapping[str, Sequence[Candle]]],
    *,
    allow_new_entries: bool,
) -> ReferenceGate | None:
    """The short-history gate of a shared bar: any covered product short of warmup gates all."""
    for product_id, window in windows.items():
        gate = _bar_seed_gate(
            strategy,
            window,
            htf_by_product[product_id],
            extra_by_product[product_id],
            allow_new_entries=allow_new_entries,
        )
        if gate is not None:
            return gate
    return None


def _decision_windows(
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    product_id: str,
    decision_candles: Sequence[Candle],
    htf_candles: Sequence[Candle],
    extra_candles: Mapping[str, Sequence[Candle]] | None,
    reference_candles: Mapping[str, Sequence[Candle]],
) -> tuple[DecisionWindow, ...]:
    """One product's planned windows and shared references, with each first bar fetched.

    Journaled on the product's decision rows (ADR 0140), so a seed can be reproduced and a
    market younger than a window shows as a ``first_bar_at`` after its ``starts_at``. The
    journal is evidence, never a trading input: a window that fails to validate is logged and
    the row records none, rather than interrupting the cycle.
    """
    try:
        return _planned_windows(
            strategy,
            deploy_anchor=deploy_anchor,
            product_id=product_id,
            decision_candles=decision_candles,
            htf_candles=htf_candles,
            extra_candles=extra_candles,
            reference_candles=reference_candles,
        )
    except ValueError:
        _logger.warning(
            "decision_windows_unavailable product_id=%s timeframe=%s",
            product_id,
            strategy.timeframe,
            exc_info=True,
        )
        return ()


def _planned_windows(
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    product_id: str,
    decision_candles: Sequence[Candle],
    htf_candles: Sequence[Candle],
    extra_candles: Mapping[str, Sequence[Candle]] | None,
    reference_candles: Mapping[str, Sequence[Candle]],
) -> tuple[DecisionWindow, ...]:
    """Pair each planned window of one product (and the references) with its first bar."""
    rows: list[DecisionWindow] = []
    for window in deployment_clock_windows(strategy, deploy_anchor=deploy_anchor):
        reference = window.role.removeprefix("reference ")
        if reference != window.role:
            fetched: Sequence[Candle] = reference_candles.get(reference, ())
        elif window.product_id != product_id:
            continue
        elif window.role == "decision clock":
            fetched = decision_candles
        elif window.role == "HTF filter":
            fetched = htf_candles
        else:
            fetched = dict(extra_candles or {}).get(window.timeframe, ())
        rows.append(
            DecisionWindow(
                role=window.role,
                product_id=window.product_id,
                timeframe=as_dataset_timeframe(parse_candle_interval(window.timeframe)),
                warmup_bars=window.warmup_bars,
                starts_at=window.starts_at,
                first_bar_at=fetched[0].starts_at if fetched else None,
            )
        )
    return tuple(rows)
