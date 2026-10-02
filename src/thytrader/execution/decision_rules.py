"""Explain an evaluated entry rule leaf by leaf for the decision journal.

Every node result comes from ``entry_condition_outcome`` on that node with the exact
merged values the runtime evaluated (``LatestEntryEvaluation``), so explanations share
the evaluator's tri-state semantics instead of re-implementing them. Operand values
are read directly from those values; nothing is recomputed from candles.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.execution.decisions import (
    ConditionComparisonTrace,
    ConditionGroupTrace,
    ConditionResult,
    DecisionOperand,
    EntryRuleTrace,
    ExitRuleTrace,
    HtfFilterTrace,
)
from thytrader.research.indicators import canonical_decimal
from thytrader.research.signal_evaluator import entry_condition_outcome
from thytrader.research.trace import EntryConditionOutcome, IndicatorTraceValue, SignalTraceRecord
from thytrader.strategies.indicator_catalog import indicator_kind_spec
from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    ComparisonOperator,
    IndicatorKind,
    IndicatorOperand,
    NotCondition,
    indicator_offset,
    operand_value_key,
    reference_instruments,
    signal_exit_condition,
    strategy_indicator_value_keys,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.execution.decisions import ConditionTrace
    from thytrader.execution.signals import LatestEntryEvaluation, LatestExitEvaluation
    from thytrader.strategies.indicator_catalog import IndicatorKindSpec
    from thytrader.strategies.models import (
        ConditionNode,
        ConditionOperand,
        IndicatorDefinition,
        ReferenceInstrument,
        StrategyDefinition,
    )

OPERATOR_SYMBOLS: dict[ComparisonOperator, str] = {
    ComparisonOperator.GT: ">",
    ComparisonOperator.GTE: "≥",
    ComparisonOperator.LT: "<",
    ComparisonOperator.LTE: "≤",
    ComparisonOperator.EQ: "=",
    ComparisonOperator.CROSSES_ABOVE: "crosses above",
    ComparisonOperator.CROSSES_BELOW: "crosses below",
}
_CROSSES = frozenset({ComparisonOperator.CROSSES_ABOVE, ComparisonOperator.CROSSES_BELOW})
OPERAND_SIGNIFICANT_DIGITS = 12
"""Operand values in a decision row are rounded to this many significant digits (ADR 0094).

Indicator Decimals carry 60+ digits that are noise to a reader; the exact values the
evaluator compared stay in the row's ``signal.indicator_values``.
"""
# Compact names for the original kinds; every other kind uses its catalog label.
_KIND_NAMES: dict[IndicatorKind, str] = {
    IndicatorKind.EMA: "EMA",
    IndicatorKind.SMA: "SMA",
    IndicatorKind.WMA: "WMA",
    IndicatorKind.RSI: "RSI",
    IndicatorKind.ATR: "ATR",
    IndicatorKind.VOLUME_SMA: "Volume SMA",
    IndicatorKind.HIGHEST: "Highest",
    IndicatorKind.LOWEST: "Lowest",
    IndicatorKind.STDEV: "StDev",
    IndicatorKind.STDEV_SAMPLE: "Sample StDev",
    IndicatorKind.ROC: "ROC",
    IndicatorKind.WILLIAMS_R: "Williams %R",
    IndicatorKind.CCI: "CCI",
    IndicatorKind.MOMENTUM: "Momentum",
    IndicatorKind.MFI: "MFI",
    IndicatorKind.MACD: "MACD",
    IndicatorKind.BOLLINGER: "BB",
    IndicatorKind.STOCHASTIC: "Stoch",
    IndicatorKind.ADX: "ADX",
}
# Output-series suffixes; an empty suffix means the kind's primary series. Unlisted
# series print their name with spaces (``senkou_a`` -> ``senkou a``).
_SERIES_NAMES: dict[str, str] = {
    "macd": "line",
    "ppo": "line",
    "tsi": "line",
    "signal": "signal",
    "histogram": "histogram",
    "middle": "middle",
    "upper": "upper",
    "lower": "lower",
    "k": "%K",
    "d": "%D",
    "adx": "",
    "plus_di": "+DI",
    "minus_di": "-DI",
    "value": "",
    "obv": "",
    "ad": "",
    "plus": "+VI",
    "minus": "-VI",
    "tenkan": "Tenkan",
    "kijun": "Kijun",
    "senkou_a": "Senkou A",
    "senkou_b": "Senkou B",
}
_RESULTS: dict[EntryConditionOutcome, ConditionResult] = {
    EntryConditionOutcome.MATCHED: ConditionResult.TRUE,
    EntryConditionOutcome.NOT_MATCHED: ConditionResult.FALSE,
    EntryConditionOutcome.UNDEFINED: ConditionResult.UNKNOWN,
}
_LABEL_LIMIT = 120


class OperandLabeler:
    """Resolve operand keys to human labels such as ``RSI(14)`` or ``BB(20,2) upper``.

    An indicator that reads a reference instrument (ADR 0096) is prefixed with that
    instrument's base currency and shows the reference clock: ``BTC · EMA(100) [1d]``.
    """

    def __init__(
        self,
        indicators: tuple[IndicatorDefinition, ...],
        timeframe: str,
        references: tuple[ReferenceInstrument, ...] = (),
    ) -> None:
        """Index declared indicators and reference instruments by id for one decision clock."""
        self._by_id = {indicator.id: indicator for indicator in indicators}
        self._timeframe = timeframe
        self._references = {reference.id: reference for reference in references}

    def label(self, operand: IndicatorOperand, *, clock: str | None = None) -> str:
        """Return a compact label with a non-decision clock (``[4h]``) and any bar lag.

        A declared ``offset`` reads ``(1 bar ago)`` / ``(3 bars ago)`` on the
        indicator's own clock, matching the strategy library summary.
        """
        indicator = self._by_id.get(operand.indicator)
        if indicator is None:
            return operand_value_key(operand)[:_LABEL_LIMIT]
        text = indicator_label(indicator, series=operand.series)
        reference = None if indicator.source is None else self._references.get(indicator.source)
        if reference is not None:
            text = f"{reference.base_currency} · {text}"
            clock = reference.timeframe
        indicator_clock = indicator.timeframe or clock
        if indicator_clock is not None and indicator_clock != self._timeframe:
            text = f"{text} [{indicator_clock}]"
        offset = indicator_offset(indicator) + (operand.offset or 0)
        if offset:
            text = f"{text} ({offset} bar{'' if offset == 1 else 's'} ago)"
        return text[:_LABEL_LIMIT]


def indicator_label(indicator: IndicatorDefinition, *, series: str | None = None) -> str:
    """Render one declared indicator (and output series) for people, e.g. ``RSI(14)``.

    Parameters follow the indicator catalog's declared order; a configurable source
    other than ``close`` is appended (``EMA(9, high)``).
    """
    if indicator.kind is IndicatorKind.IDENTITY:
        source = indicator.input if isinstance(indicator.input, str) else "close"
        return source.capitalize()
    if indicator.kind is IndicatorKind.CONSTANT:
        value = getattr(indicator.parameters, "value", None)
        return str(value) if value is not None else indicator.id
    spec = indicator_kind_spec(indicator.kind)
    name = _KIND_NAMES.get(indicator.kind, spec.label)
    arguments = _parameter_text(indicator, spec)
    source = indicator.input
    if spec.input_mode == "configurable" and isinstance(source, str) and source != "close":
        arguments = f"{arguments}, {source}" if arguments else source
    text = f"{name}({arguments})" if arguments else name
    if series is None:
        return text
    suffix = _SERIES_NAMES.get(series, series.replace("_", " "))
    return f"{text} {suffix}" if suffix else text


def _parameter_text(indicator: IndicatorDefinition, spec: IndicatorKindSpec) -> str:
    """Render the declared parameter values in the catalog's order (unset optionals omitted)."""
    declared = indicator.parameters.model_dump(mode="json")
    return ",".join(
        str(declared[parameter.name])
        for parameter in spec.parameters
        if declared.get(parameter.name) is not None
    )


def entry_rule_trace(
    strategy: StrategyDefinition, evaluation: LatestEntryEvaluation
) -> EntryRuleTrace | None:
    """Explain the evaluated entry tree, HTF filter, and indicator values of one bar.

    Returns None when the evaluation had no bar at all (no candles).
    """
    if evaluation.candle_starts_at is None:
        return None
    labeler = OperandLabeler(
        strategy.indicators, strategy.timeframe, reference_instruments(strategy)
    )
    entry = condition_trace(
        strategy.entry.when, evaluation.current, evaluation.previous, labeler=labeler
    )
    htf_filter = strategy.htf_filter
    htf_trace = None
    if htf_filter is not None and evaluation.htf_outcome is not None:
        htf_labeler = OperandLabeler(htf_filter.indicators, strategy.timeframe)
        htf_trace = HtfFilterTrace(
            timeframe=htf_filter.timeframe,
            outcome=evaluation.htf_outcome,
            condition=condition_trace(
                htf_filter.when,
                evaluation.htf_current,
                evaluation.htf_previous,
                labeler=htf_labeler,
                clock=htf_filter.timeframe,
            ),
        )
    return EntryRuleTrace(
        outcome=evaluation.outcome,
        entry=entry,
        htf_filter=htf_trace,
        signal=signal_record(strategy, evaluation),
    )


def exit_rule_trace(
    strategy: StrategyDefinition, evaluation: LatestExitEvaluation
) -> ExitRuleTrace | None:
    """Explain the evaluated ``exits.signal_exit`` tree of one bar (ADR 0093).

    Returns None when the strategy declares no signal exit or the evaluation had no bar.
    """
    condition = signal_exit_condition(strategy.exits)
    if condition is None or evaluation.candle_starts_at is None:
        return None
    labeler = OperandLabeler(
        strategy.indicators, strategy.timeframe, reference_instruments(strategy)
    )
    return ExitRuleTrace(
        outcome=evaluation.outcome,
        condition=condition_trace(
            condition, evaluation.current, evaluation.previous, labeler=labeler
        ),
    )


def signal_record(
    strategy: StrategyDefinition, evaluation: LatestEntryEvaluation
) -> SignalTraceRecord | None:
    """Reuse the research ``SignalTraceRecord`` shape for this bar's indicator values."""
    if evaluation.candle_starts_at is None:
        return None
    keys = strategy_indicator_value_keys(strategy)
    if not keys:
        return None
    values = tuple(
        IndicatorTraceValue(
            indicator_id=key,
            value=_canonical_or_none(_read(evaluation.current, evaluation.htf_current, key)),
        )
        for key in keys
    )
    try:
        return SignalTraceRecord(
            candle_starts_at=evaluation.candle_starts_at,
            indicator_values=values,
            entry_condition=evaluation.outcome,
        )
    except ValidationError:
        return None


def _read(
    current: Mapping[str, Decimal | None], htf: Mapping[str, Decimal | None], key: str
) -> Decimal | None:
    """Read one LTF/extra-TF key, falling back to the HTF filter values."""
    if key in current:
        return current[key]
    return htf.get(key)


def condition_trace(
    condition: ConditionNode,
    current: Mapping[str, Decimal | None],
    previous: Mapping[str, Decimal | None] | None,
    *,
    labeler: OperandLabeler,
    clock: str | None = None,
) -> ConditionTrace:
    """Explain one rule node recursively with the evaluator's own tri-state result."""
    result = _RESULTS[entry_condition_outcome(condition, current, previous)]
    if isinstance(condition, ComparisonCondition):
        crossover = condition.operator in _CROSSES
        left = _operand(condition.left, current, previous, labeler, clock, crossover=crossover)
        right = _operand(condition.right, current, previous, labeler, clock, crossover=crossover)
        symbol = OPERATOR_SYMBOLS[condition.operator]
        return ConditionComparisonTrace(
            result=result,
            label=f"{left.label} {symbol} {right.label}"[:300],
            operator=condition.operator,
            operator_symbol=symbol,
            left=left,
            right=right,
        )
    if isinstance(condition, NotCondition):
        child = condition_trace(condition.not_, current, previous, labeler=labeler, clock=clock)
        return ConditionGroupTrace(node="not", result=result, children=(child,))
    is_all = isinstance(condition, AllCondition)
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    return ConditionGroupTrace(
        node="all" if is_all else "any",
        result=result,
        children=tuple(
            condition_trace(child, current, previous, labeler=labeler, clock=clock)
            for child in children
        ),
    )


def _operand(
    operand: ConditionOperand,
    current: Mapping[str, Decimal | None],
    previous: Mapping[str, Decimal | None] | None,
    labeler: OperandLabeler,
    clock: str | None,
    *,
    crossover: bool,
) -> DecisionOperand:
    """Describe one operand with its current (and, for crossovers, previous) value."""
    if not isinstance(operand, IndicatorOperand):
        literal = _canonical_text(operand.literal)
        return DecisionOperand(kind="literal", label=literal, value=literal)
    key = operand_value_key(operand)
    previous_value = None
    if crossover and previous is not None:
        previous_value = significant_or_none(previous.get(key))
    return DecisionOperand(
        kind="indicator",
        label=labeler.label(operand, clock=clock),
        key=key,
        value=significant_or_none(current.get(key)),
        previous_value=previous_value,
    )


def significant_or_none(
    value: Decimal | None, digits: int = OPERAND_SIGNIFICANT_DIGITS
) -> str | None:
    """Canonical Decimal text rounded half-even to ``digits`` significant digits.

    ``64123.45678912345678901234`` becomes ``64123.4567891``; None and non-finite
    values stay undefined.
    """
    if value is None or not value.is_finite():
        return None
    if value.is_zero():
        return "0"
    quantum = Decimal(1).scaleb(value.adjusted() - (digits - 1))
    try:
        return canonical_decimal(value.quantize(quantum, rounding=ROUND_HALF_EVEN))
    except InvalidOperation:
        return canonical_decimal(value)


def _canonical_or_none(value: Decimal | None) -> str | None:
    """Canonical Decimal text, or None for an undefined value."""
    if value is None or not value.is_finite():
        return None
    return canonical_decimal(value)


def _canonical_text(text: str) -> str:
    """Canonicalize one literal DecimalText (``50.0`` -> ``50``)."""
    try:
        return canonical_decimal(Decimal(text))
    except InvalidOperation:
        return text


def display_decimal(text: str | None) -> str:
    """Round a canonical Decimal string for one-line summaries (4 significant digits).

    Large values keep two decimals (``64123.45``); small ones keep enough decimals to
    show four significant digits (``0.00001235``). Exact values stay in the record.
    """
    if text is None:
        return "n/a"
    try:
        value = Decimal(text)
    except InvalidOperation:
        return text
    if value.is_zero():
        return "0"
    decimals = max(2, 3 - value.adjusted())
    quantum = Decimal(1).scaleb(-decimals)
    try:
        rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return text
    return canonical_decimal(rounded)


def first_unmet(node: ConditionTrace) -> ConditionTrace | None:
    """Return the first node that explains a false/unknown result, depth-first.

    The evaluator makes a group unknown when any child is unknown and false only
    when every child is defined, so descending into the first child that shares the
    group's result finds the deciding leaf. A false NOT is itself the explanation
    (its child held).
    """
    if node.result is ConditionResult.TRUE:
        return None
    if isinstance(node, ConditionComparisonTrace):
        return node
    if node.node == "not":
        if node.result is ConditionResult.UNKNOWN:
            return first_unmet(node.children[0]) or node
        return node
    for child in node.children:
        if child.result is node.result:
            return first_unmet(child) or child
    return node


def count_unmet_leaves(node: ConditionTrace) -> int:
    """Count leaves (or NOT nodes) that are not true under a node."""
    if node.result is ConditionResult.TRUE:
        return 0
    if isinstance(node, ConditionComparisonTrace) or node.node == "not":
        return 1
    return sum(count_unmet_leaves(child) for child in node.children)


def unmet_text(node: ConditionTrace) -> str:
    """Describe why one unmet node failed, e.g. ``RSI(14) 47.21 needs ≥ 50``."""
    if isinstance(node, ConditionGroupTrace):
        if node.node == "not" and node.children:
            inner = node.children[0]
            return f"{_leaf_values(inner)} must not hold"
        return "rule group not met"
    if node.result is ConditionResult.UNKNOWN:
        missing = node.left if node.left.value is None else node.right
        return f"{missing.label} has no value yet"
    if node.operator in _CROSSES:
        return _no_cross_text(node)
    return (
        f"{node.left.label} {display_decimal(node.left.value)} needs "
        f"{node.operator_symbol} {_right_text(node.right)}"
    )


def _no_cross_text(node: ConditionComparisonTrace) -> str:
    """Explain a false cross from where the lines are now, not only that none happened.

    ``EMA(9) is above EMA(21); no new cross this bar`` when fast already sits above slow,
    and ``EMA(9) is below EMA(21); no cross above yet`` when it has not reached it.
    """
    values = f"({display_decimal(node.left.value)} vs {display_decimal(node.right.value)})"
    direction = "above" if node.operator is ComparisonOperator.CROSSES_ABOVE else "below"
    side = _relative_side(node.left.value, node.right.value)
    if side is None:
        return f"{node.left.label} did not cross {direction} {node.right.label} {values}"
    if side == direction:
        return f"{node.left.label} is {side} {node.right.label}; no new cross this bar {values}"
    return f"{node.left.label} is {side} {node.right.label}; no cross {direction} yet {values}"


def _relative_side(left: str | None, right: str | None) -> str | None:
    """``above`` / ``below`` / ``at`` for two operand values, or None when unknown."""
    if left is None or right is None:
        return None
    try:
        difference = Decimal(left) - Decimal(right)
    except InvalidOperation:
        return None
    if difference > 0:
        return "above"
    if difference < 0:
        return "below"
    return "at"


def met_text(node: ConditionTrace, *, fallback: str = "entry rule matched") -> str:
    """Describe a satisfied rule compactly, e.g. ``RSI(14) 55.2 ≥ 50``.

    ``fallback`` names the rule when no leaf summarizes it (the exit rule passes its own).
    """
    if isinstance(node, ConditionComparisonTrace):
        return _leaf_values(node)
    leaves = [child for child in node.children if isinstance(child, ConditionComparisonTrace)]
    if node.node == "all" and leaves:
        first = _leaf_values(leaves[0])
        return first if len(node.children) == 1 else f"{first} (+{len(node.children) - 1} more)"
    if node.node == "any":
        held = next(
            (child for child in node.children if child.result is ConditionResult.TRUE), None
        )
        if held is not None:
            return met_text(held, fallback=fallback)
    return fallback


def _leaf_values(node: ConditionTrace) -> str:
    """Render one comparison with its values, or a group's node kind."""
    if isinstance(node, ConditionGroupTrace):
        return node.node.upper()
    return (
        f"{node.left.label} {display_decimal(node.left.value)} {node.operator_symbol} "
        f"{_right_text(node.right)}"
    )


def _right_text(operand: DecisionOperand) -> str:
    """Literals print once (``50``); indicator operands print label and value."""
    if operand.kind == "literal":
        return operand.label
    return f"{operand.label} {display_decimal(operand.value)}"
