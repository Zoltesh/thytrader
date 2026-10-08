"""Evaluate the latest closed candle's entry and signal-exit conditions for paper/live."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.evaluation.indicators import calculate_indicator_rows
from thytrader.evaluation.multi_timeframe import (
    bars_closed_at_or_before,
    ltf_close,
    mapped_htf_start,
)
from thytrader.evaluation.signal_evaluator import (
    SignalEvaluationError,
    and_entry_outcomes,
    calculate_extra_indicator_rows,
    calculate_htf_indicator_rows,
    calculate_reference_indicator_rows,
    entry_condition_outcome,
    htf_filter_outcome,
    overlay_indicator_timeframe_values,
)
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.strategies.models import (
    decision_clock_indicators,
    reference_instruments,
    signal_exit_condition,
    strategy_indicator_operands,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


@dataclass(frozen=True, slots=True)
class LatestEntryEvaluation:
    """One newest-bar entry outcome plus the exact values the rule consumed.

    ``current``/``previous`` are the merged decision-clock (extra-TF and reference
    instrument) values the entry tree read; ``htf_current``/``htf_previous`` are the
    last-completed HTF values
    the optional filter read. The decision journal explains a bar from these values
    instead of recomputing indicators. ``candle_starts_at`` is None only without candles.
    """

    outcome: EntryConditionOutcome
    candle_starts_at: datetime | None
    ltf_outcome: EntryConditionOutcome
    current: Mapping[str, Decimal | None] = field(default_factory=dict)
    previous: Mapping[str, Decimal | None] | None = None
    htf_outcome: EntryConditionOutcome | None = None
    htf_current: Mapping[str, Decimal | None] = field(default_factory=dict)
    htf_previous: Mapping[str, Decimal | None] | None = None


@dataclass(frozen=True, slots=True)
class LatestExitEvaluation:
    """One newest-bar ``exits.signal_exit`` outcome plus the values the rule read (ADR 0093).

    ``current``/``previous`` are the merged decision-clock (and extra-TF) values, the same
    ones the entry rule reads on that bar; the HTF filter never gates an exit.
    ``candle_starts_at`` is None only without candles.
    """

    outcome: EntryConditionOutcome
    candle_starts_at: datetime | None
    current: Mapping[str, Decimal | None] = field(default_factory=dict)
    previous: Mapping[str, Decimal | None] | None = None


def evaluate_latest_signal_exit(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    *,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> LatestExitEvaluation | None:
    """Evaluate ``exits.signal_exit`` on the newest closed LTF bar, or None without one.

    Uses exactly the merged values ``evaluate_latest_entry_evidence`` computes for
    ``entry.when`` (extra-TF indicators from last-completed bars only); HTF candles are
    read only when an extra-TF indicator shares the filter's clock. Missing required
    extra-TF coverage raises ``SignalEvaluationError`` (fail closed: no exit is invented).
    A reference instrument without closed-bar coverage leaves its values undefined, so a
    rule reading it cannot match (ADR 0096).
    """
    condition = signal_exit_condition(strategy.exits)
    if condition is None:
        return None
    if len(candles) < 2:
        return LatestExitEvaluation(
            outcome=EntryConditionOutcome.UNDEFINED,
            candle_starts_at=candles[-1].starts_at if candles else None,
        )
    merged_values, merged_previous = _latest_merged_values(
        strategy,
        candles,
        _visible_htf(strategy, candles, htf_candles),
        indicator_timeframe_candles,
        reference_candles,
    )
    return LatestExitEvaluation(
        outcome=entry_condition_outcome(condition, merged_values, merged_previous),
        candle_starts_at=candles[-1].starts_at,
        current=merged_values,
        previous=merged_previous,
    )


def _visible_htf(
    strategy: StrategyDefinition, candles: Sequence[Candle], htf_candles: Sequence[Candle]
) -> Sequence[Candle]:
    """HTF bars that had closed by the newest LTF close (all of them without a filter)."""
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        return htf_candles
    return bars_closed_at_or_before(
        htf_candles,
        close_at=ltf_close(candles[-1].starts_at, strategy.timeframe),
        timeframe=htf_filter.timeframe,
    )


def _latest_merged_values(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    visible_htf: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> tuple[dict[str, Decimal | None], dict[str, Decimal | None] | None]:
    """Decision-clock values of the newest two bars with extra-TF values held onto them.

    Reference instrument bars are filtered to those closed at or before the newest
    decision close before any indicator is computed, so an in-progress reference bar is
    never read (ADR 0096).
    """
    latest = candles[-1]
    current_close = ltf_close(latest.starts_at, strategy.timeframe)
    visible_extra = {
        timeframe: bars_closed_at_or_before(bars, close_at=current_close, timeframe=timeframe)
        for timeframe, bars in dict(indicator_timeframe_candles or {}).items()
    }
    extra_rows = calculate_extra_indicator_rows(
        strategy,
        visible_extra,
        visible_htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=current_close,
    )
    reference_rows = calculate_reference_indicator_rows(
        strategy,
        {
            reference.id: bars_closed_at_or_before(
                (reference_candles or {}).get(reference.id, ()),
                close_at=current_close,
                timeframe=reference.timeframe,
            )
            for reference in reference_instruments(strategy)
        },
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=current_close,
        strict=False,
    )
    rows = calculate_indicator_rows(
        decision_clock_indicators(strategy),
        candles,
        operands=strategy_indicator_operands(strategy),
    )
    return overlay_indicator_timeframe_values(
        strategy,
        latest,
        previous_ltf_start=candles[-2].starts_at,
        ltf_values=rows[-1],
        previous_ltf_values=rows[-2],
        extra_rows=extra_rows,
        reference_rows=reference_rows,
        strict_references=False,
    )


def evaluate_latest_entry(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    *,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> EntryConditionOutcome:
    """Evaluate LTF entry AND optional HTF filter on the newest closed LTF bar.

    Extra-TF LTF-list indicators, reference-instrument indicators, and HTF-filter values
    come from last-completed bars only. In-progress bars are dropped. Missing required
    coverage fails closed (a missing reference leaves its values undefined: no entry).
    """
    return evaluate_latest_entry_evidence(
        strategy,
        candles,
        htf_candles,
        indicator_timeframe_candles,
        reference_candles=reference_candles,
    ).outcome


def evaluate_latest_entry_evidence(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    *,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> LatestEntryEvaluation:
    """Evaluate like ``evaluate_latest_entry`` and keep the values each rule read.

    The outcome is computed by the same calls in the same order, so trading semantics
    are identical; only the intermediate merged values are retained for explanation.
    """
    htf_filter = strategy.htf_filter
    if htf_filter is None and htf_candles:
        raise SignalEvaluationError("HTF candles were supplied without an HTF filter.")
    if htf_filter is not None and not htf_candles:
        raise SignalEvaluationError("HTF candles are required for an HTF-filter strategy.")
    if len(candles) < 2:
        return LatestEntryEvaluation(
            outcome=EntryConditionOutcome.UNDEFINED,
            candle_starts_at=candles[-1].starts_at if candles else None,
            ltf_outcome=EntryConditionOutcome.UNDEFINED,
        )
    latest = candles[-1]
    current_close = ltf_close(latest.starts_at, strategy.timeframe)
    visible_htf = _visible_htf(strategy, candles, htf_candles)
    merged_values, merged_previous = _latest_merged_values(
        strategy, candles, visible_htf, indicator_timeframe_candles, reference_candles
    )
    ltf_outcome = entry_condition_outcome(strategy.entry.when, merged_values, merged_previous)
    if htf_filter is None:
        return LatestEntryEvaluation(
            outcome=ltf_outcome,
            candle_starts_at=latest.starts_at,
            ltf_outcome=ltf_outcome,
            current=merged_values,
            previous=merged_previous,
        )
    htf_rows = calculate_htf_indicator_rows(
        strategy,
        visible_htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=current_close,
    )
    htf_outcome, htf_values = htf_filter_outcome(
        strategy,
        latest,
        previous_ltf_start=candles[-2].starts_at,
        htf_rows=htf_rows,
    )
    previous_htf_start = mapped_htf_start(
        ltf_close(candles[-2].starts_at, strategy.timeframe), htf_filter.timeframe
    )
    return LatestEntryEvaluation(
        outcome=and_entry_outcomes(ltf_outcome, htf_outcome),
        candle_starts_at=latest.starts_at,
        ltf_outcome=ltf_outcome,
        current=merged_values,
        previous=merged_previous,
        htf_outcome=htf_outcome,
        htf_current=htf_values,
        htf_previous=htf_rows.get(previous_htf_start),
    )


def latest_atr(strategy: StrategyDefinition, candles: Sequence[Candle]) -> Decimal | None:
    """Return the newest ATR value used by the initial stop, or None when undefined."""
    return named_atr(strategy, candles, strategy.exits.initial_stop.atr_indicator)


def named_atr(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    indicator_id: str,
) -> Decimal | None:
    """Return the newest value of one named ATR, or None when undefined."""
    rows = calculate_indicator_rows(decision_clock_indicators(strategy), candles)
    if not rows:
        return None
    value = rows[-1].get(indicator_id)
    return value if isinstance(value, Decimal) else None
