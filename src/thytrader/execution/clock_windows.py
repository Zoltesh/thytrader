"""Deploy-anchored clock windows a paper or live book reads (ADR 0113, ADR 0140).

Every clock a strategy reads (the decision clock of each covered product, the HTF filter,
per-indicator extra timeframes and reference instruments) is fetched from a fixed start
before the deployment's creation. The worker sizes those windows here, and a start checks
that the venue's history reaches each start, so both agree on what the book needs. An
entry decision under the convergence policy also confirms its windows hold that warmup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thytrader.evaluation.models import warmup_starts_at
from thytrader.evaluation.multi_timeframe import closed_bar_required_coverage, ltf_close
from thytrader.market_data.models import parse_candle_interval
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    lockstep_product_ids,
    reference_data_requirements,
    strategy_indicator_operands,
)
from thytrader.trading.geometry import entry_bar_bucket

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


@dataclass(frozen=True, slots=True)
class ClockWindow:
    """One deploy-anchored window: its series, its warmup and the bar it starts at.

    ``role`` names the clock for messages ("decision clock", "HTF filter", "indicator
    timeframe" or "reference <id>").
    """

    product_id: str
    timeframe: str
    warmup_bars: int
    starts_at: datetime
    role: str


def required_clock_warmup_bars(
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


def extra_timeframe_warmup(strategy: StrategyDefinition, timeframe: str) -> int | None:
    """The warmup of the extra-timeframe indicators on one clock, or None without any."""
    for clock, indicators in extra_indicator_timeframe_groups(strategy):
        if clock == timeframe:
            return extra_indicator_timeframe_warmup(
                indicators,
                operands=strategy_indicator_operands(strategy),
                policy=strategy.data_requirements.warmup_policy,
            )
    return None


def shared_clock_union_warmup(
    strategy: StrategyDefinition, *, timeframe: str, warmup_bars: int, deploy_anchor: datetime
) -> int:
    """Cover the filter clock and every extra-timeframe indicator sharing it (ADR 0113).

    The shared window is fetched once at the union length. Each consumer's indicator rows
    are still selected from its own exact required bars, so the longer window does not move
    a seeded indicator value.
    """
    needed = required_clock_warmup_bars(
        strategy, timeframe=timeframe, warmup_bars=warmup_bars, deploy_anchor=deploy_anchor
    )
    extra = extra_timeframe_warmup(strategy, timeframe)
    if extra is not None:
        needed = max(
            needed,
            required_clock_warmup_bars(
                strategy, timeframe=timeframe, warmup_bars=extra, deploy_anchor=deploy_anchor
            ),
        )
    return needed


def deployment_clock_windows(
    strategy: StrategyDefinition, *, deploy_anchor: datetime
) -> tuple[ClockWindow, ...]:
    """Every window a book of this strategy fetches, sized exactly as the worker sizes it."""
    windows: list[ClockWindow] = []
    htf_filter = strategy.htf_filter
    for product_id in lockstep_product_ids(strategy):
        windows.append(
            _window(
                product_id,
                strategy.timeframe,
                strategy.data_requirements.warmup_bars,
                deploy_anchor,
                "decision clock",
            )
        )
        if htf_filter is not None:
            union = shared_clock_union_warmup(
                strategy,
                timeframe=htf_filter.timeframe,
                warmup_bars=htf_filter.data_requirements.warmup_bars,
                deploy_anchor=deploy_anchor,
            )
            windows.append(
                _window(product_id, htf_filter.timeframe, union, deploy_anchor, "HTF filter")
            )
        for timeframe, _indicators in extra_indicator_timeframe_groups(strategy):
            if htf_filter is not None and timeframe == htf_filter.timeframe:
                continue
            warmup = required_clock_warmup_bars(
                strategy,
                timeframe=timeframe,
                warmup_bars=extra_timeframe_warmup(strategy, timeframe) or 0,
                deploy_anchor=deploy_anchor,
            )
            windows.append(
                _window(product_id, timeframe, warmup, deploy_anchor, "indicator timeframe")
            )
    for requirement in reference_data_requirements(strategy):
        warmup = required_clock_warmup_bars(
            strategy,
            timeframe=requirement.timeframe,
            warmup_bars=requirement.warmup_bars,
            deploy_anchor=deploy_anchor,
        )
        windows.append(
            _window(
                requirement.product_id,
                requirement.timeframe,
                warmup,
                deploy_anchor,
                f"reference {requirement.reference_id}",
            )
        )
    return tuple(windows)


def _window(
    product_id: str, timeframe: str, warmup_bars: int, deploy_anchor: datetime, role: str
) -> ClockWindow:
    """One window starting ``warmup_bars`` before the anchor's bucket on its clock."""
    return ClockWindow(
        product_id=product_id,
        timeframe=timeframe,
        warmup_bars=warmup_bars,
        starts_at=warmup_starts_at(
            entry_bar_bucket(deploy_anchor, timeframe), warmup_bars, timeframe
        ),
        role=role,
    )


def windows_hold_warmup(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
) -> bool:
    """Whether every clock window of one decision bar reaches back over its warmup (ADR 0140).

    ``candles`` ends with the decision bar and must hold its warmup. An HTF or extra-timeframe
    window must start at or before the evaluator's own required coverage start for that bar,
    which also covers the previous mapped bar at a clock rollover. A book's windows start a
    full warmup before its creation, so this fails only where the venue had no candles that
    early (a market listed after the window start, or a quiet first bar): indicators would be
    seeded late, so entries wait until each window covers its warmup. That holds for every
    snapshot, measured against its own declared warmups: the convergence policy only sets how
    long they are, and a snapshot saved before it may start with a warning (ADR 0142).
    Coverage through the latest mapped bar and contiguity are the worker's gap checks, not this
    one.
    """
    if len(candles) < max(strategy.data_requirements.warmup_bars, 1):
        return False
    latest = candles[-1].starts_at
    decision_close = ltf_close(latest, strategy.timeframe)
    required: list[tuple[Sequence[Candle], str, int]] = []
    htf_filter = strategy.htf_filter
    if htf_filter is not None:
        required.append(
            (htf_candles, htf_filter.timeframe, htf_filter.data_requirements.warmup_bars)
        )
    for timeframe, bars in dict(indicator_timeframe_candles or {}).items():
        warmup = extra_timeframe_warmup(strategy, timeframe)
        if warmup is not None:
            required.append((bars, timeframe, warmup))
    for bars, timeframe, warmup in required:
        required_start, _end = closed_bar_required_coverage(
            evaluation_starts_at=latest,
            evaluation_ends_at=decision_close,
            timeframe=timeframe,
            warmup_bars=warmup,
        )
        if not bars or bars[0].starts_at > required_start:
            return False
    return True
