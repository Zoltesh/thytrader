"""Bounded human-readable outlines of validated strategy definitions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from thytrader.strategies.indicator_catalog import indicator_kind_spec
from thytrader.strategies.models import (
    AllCondition,
    AnyCondition,
    BollingerIndicatorParameters,
    ComparisonCondition,
    ComparisonOperator,
    ConditionOperand,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    IndicatorParameters,
    LiteralOperand,
    MacdIndicatorParameters,
    NotCondition,
    ReferenceInstrument,
    StochasticIndicatorParameters,
    StrategyDefinition,
    indicator_offset,
    reference_instruments,
    signal_exit_condition,
)


@dataclass(frozen=True, slots=True)
class _Operands:
    """Declared indicators and reference instruments an operand summary may name."""

    indicators: dict[str, IndicatorDefinition]
    references: dict[str, ReferenceInstrument]


def _operands(definition: StrategyDefinition) -> _Operands:
    """Index one definition's indicators and reference instruments by id."""
    return _Operands(
        indicators={indicator.id: indicator for indicator in definition.indicators},
        references={reference.id: reference for reference in reference_instruments(definition)},
    )


def strategy_summary(definition: StrategyDefinition) -> str:
    """Render a bounded human-readable outline from validated strategy semantics.

    A declared ``exits.signal_exit`` rule follows the entry rule as ``exit when …``.
    The notional range is labeled with the instrument's quote currency (USD, USDC,
    USDT) rather than a dollar sign, so stablecoin strategies read correctly. A
    reference-instrument operand reads ``BTC · EMA(100) [1d]`` and the outline names
    each read-only reference series (``reads BTC-USDC 1d``; ADR 0096).
    """
    entry_summary = _entry_rule_summary(definition)
    exit_summary = _exit_rule_summary(definition)
    risk_text = _shift_decimal_text(definition.sizing.risk_fraction, places=2)
    return (
        f"{definition.instrument.product_id} · {definition.timeframe} · {entry_summary} · "
        f"{exit_summary}{_reference_summary(definition)}{risk_text}% risk · "
        f"{definition.sizing.min_quote_notional}-{definition.sizing.max_quote_notional} "
        f"{definition.instrument.quote_currency}"
    )


def _entry_rule_summary(definition: StrategyDefinition) -> str:
    """Describe the validated entry rule tree without EMA-only assumptions."""
    return _condition_summary(definition.entry.when, _operands(definition))


def _reference_summary(definition: StrategyDefinition) -> str:
    """``reads BTC-USDC 1d · `` for reference instruments, or nothing without any."""
    references = reference_instruments(definition)
    if not references:
        return ""
    series = ", ".join(f"{item.product_id} {item.timeframe}" for item in references)
    return f"reads {series} · "


def _exit_rule_summary(definition: StrategyDefinition) -> str:
    """``exit when … · ``, or nothing when the strategy declares no signal exit (ADR 0093)."""
    condition = signal_exit_condition(definition.exits)
    if condition is None:
        return ""
    return f"exit when {_condition_summary(condition, _operands(definition))} · "


def _condition_summary(
    condition: ComparisonCondition | AllCondition | AnyCondition | NotCondition,
    operands: _Operands,
) -> str:
    """Flatten one validated condition tree into bounded operator-readable text."""
    if isinstance(condition, ComparisonCondition):
        return _comparison_summary(condition, operands)
    if isinstance(condition, NotCondition):
        return f"NOT ({_condition_summary(condition.not_, operands)})"
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    joiner = " AND " if isinstance(condition, AllCondition) else " OR "
    return joiner.join(_child_summary(child, joiner, operands) for child in children)


def _child_summary(
    child: ComparisonCondition | AllCondition | AnyCondition | NotCondition,
    parent_joiner: str,
    operands: _Operands,
) -> str:
    """Parenthesize a nested group whose joiner differs, so A AND (B OR C) stays exact."""
    text = _condition_summary(child, operands)
    if isinstance(child, AllCondition) and parent_joiner != " AND ":
        return f"({text})"
    if isinstance(child, AnyCondition) and parent_joiner != " OR ":
        return f"({text})"
    return text


def _comparison_summary(
    condition: ComparisonCondition,
    operands: _Operands,
) -> str:
    """Render one comparison or crossover from its validated operands."""
    left = _operand_summary(condition.left, operands)
    right = _operand_summary(condition.right, operands)
    if condition.operator is ComparisonOperator.CROSSES_ABOVE:
        return f"{left} crosses above {right}"
    if condition.operator is ComparisonOperator.CROSSES_BELOW:
        return f"{left} crosses below {right}"
    symbol = _COMPARISON_SYMBOLS[condition.operator]
    return f"{left} {symbol} {right}"


_COMPARISON_SYMBOLS = {
    ComparisonOperator.GT: ">",
    ComparisonOperator.GTE: "≥",
    ComparisonOperator.LT: "<",
    ComparisonOperator.LTE: "≤",
    ComparisonOperator.EQ: "=",
}


def _operand_summary(operand: ConditionOperand, operands: _Operands) -> str:
    """Render one indicator or literal operand for summary text.

    A reference-instrument indicator is prefixed with that instrument's base currency
    and suffixed with its clock: ``BTC · EMA(100) [1d]``.
    """
    if isinstance(operand, LiteralOperand):
        return operand.literal
    indicator = operands.indicators[operand.indicator]
    text = _indicator_operand_summary(operand, indicator)
    reference = None if indicator.source is None else operands.references.get(indicator.source)
    if reference is None:
        return text
    return f"{reference.base_currency} · {text} [{reference.timeframe}]"


_PERIOD_KIND_LABELS: dict[IndicatorKind, str] = {
    IndicatorKind.EMA: "EMA",
    IndicatorKind.SMA: "SMA",
    IndicatorKind.WMA: "WMA",
    IndicatorKind.RSI: "RSI",
    IndicatorKind.ROC: "ROC",
}
_HISTORICAL_SUMMARY_KINDS = frozenset(
    {
        IndicatorKind.EMA,
        IndicatorKind.SMA,
        IndicatorKind.RSI,
        IndicatorKind.ATR,
        IndicatorKind.VOLUME_SMA,
        IndicatorKind.HIGHEST,
        IndicatorKind.LOWEST,
        IndicatorKind.STDEV,
        IndicatorKind.ROC,
        IndicatorKind.WILLIAMS_R,
        IndicatorKind.CCI,
        IndicatorKind.IDENTITY,
        IndicatorKind.CONSTANT,
        IndicatorKind.WMA,
        IndicatorKind.MOMENTUM,
        IndicatorKind.MFI,
        IndicatorKind.MACD,
        IndicatorKind.BOLLINGER,
        IndicatorKind.STDEV_SAMPLE,
        IndicatorKind.STOCHASTIC,
        IndicatorKind.ADX,
    }
)
_CATALOG_LABELED_KINDS = frozenset(IndicatorKind) - _HISTORICAL_SUMMARY_KINDS
"""Kinds summarized as ``Label(parameters) series``; historical kinds keep their wording."""


def _multi_series_indicator_label(
    operand: IndicatorOperand,
    indicator: IndicatorDefinition,
) -> str | None:
    """Return a label for multi-output indicator kinds when recognized."""
    if indicator.kind is IndicatorKind.MACD and isinstance(
        indicator.parameters, MacdIndicatorParameters
    ):
        if operand.series == "signal":
            return "MACD signal"
        if operand.series == "histogram":
            return "MACD histogram"
        return "MACD line"
    if indicator.kind is IndicatorKind.BOLLINGER and isinstance(
        indicator.parameters, BollingerIndicatorParameters
    ):
        series = operand.series or "middle"
        if series == "upper":
            return "upper Bollinger band"
        if series == "lower":
            return "lower Bollinger band"
        return "middle Bollinger band"
    if indicator.kind is IndicatorKind.STOCHASTIC and isinstance(
        indicator.parameters, StochasticIndicatorParameters
    ):
        series = operand.series or "k"
        return "%K" if series == "k" else "%D"
    if indicator.kind is IndicatorKind.ADX and isinstance(
        indicator.parameters, IndicatorParameters
    ):
        series = operand.series or "adx"
        if series == "adx":
            return f"ADX({indicator.parameters.period})"
        return f"{series.upper()}({indicator.parameters.period})"
    return None


def _indicator_operand_summary(
    operand: IndicatorOperand,
    indicator: IndicatorDefinition,
) -> str:
    """Render the indicator read with its combined declaration and operand native-clock lag."""
    text = _unlagged_operand_summary(operand, indicator)
    offset = indicator_offset(indicator) + (operand.offset or 0)
    if offset == 0:
        return text
    return f"{text} ({offset} bar{'' if offset == 1 else 's'} ago)"


def _unlagged_operand_summary(
    operand: IndicatorOperand,
    indicator: IndicatorDefinition,
) -> str:
    """Render one indicator reference without its declared bar lag."""
    if isinstance(indicator.parameters, IndicatorParameters):
        period_label = _PERIOD_KIND_LABELS.get(indicator.kind)
        if period_label is not None:
            return f"{period_label}({indicator.parameters.period})"
    multi_series = _multi_series_indicator_label(operand, indicator)
    if multi_series is not None:
        return multi_series
    if indicator.kind is IndicatorKind.IDENTITY:
        return str(indicator.input)
    if indicator.kind in _CATALOG_LABELED_KINDS:
        return _catalog_operand_label(operand, indicator)
    if operand.series is None:
        return operand.indicator
    return f"{operand.indicator}.{operand.series}"


def _catalog_operand_label(operand: IndicatorOperand, indicator: IndicatorDefinition) -> str:
    """Render ``Label(parameters) series`` for kinds added with the wider catalog."""
    spec = indicator_kind_spec(indicator.kind)
    declared = indicator.parameters.model_dump(mode="json")
    values = [
        str(declared[parameter.name])
        for parameter in spec.parameters
        if declared.get(parameter.name) is not None
    ]
    configurable_source = isinstance(indicator.input, str) and spec.input_mode == "configurable"
    if configurable_source and indicator.input != "close":
        values.insert(0, str(indicator.input))
    label = f"{spec.label}({', '.join(values)})"
    return label if operand.series is None else f"{label} {operand.series}"


def _shift_decimal_text(value: str, *, places: int) -> str:
    """Shift an exact canonical decimal point without ambient-context arithmetic."""
    sign, digits, exponent = Decimal(value).as_tuple()
    if not isinstance(exponent, int):
        raise TypeError("Strategy summary requires a finite decimal risk fraction.")
    digit_text = "".join(str(digit) for digit in digits) or "0"
    shifted_exponent = exponent + places
    if shifted_exponent >= 0:
        result = digit_text + ("0" * shifted_exponent)
    else:
        point = len(digit_text) + shifted_exponent
        if point <= 0:
            result = f"0.{('0' * -point)}{digit_text}"
        else:
            result = f"{digit_text[:point]}.{digit_text[point:]}"
    result = result.rstrip("0").rstrip(".") if "." in result else result
    unsigned = result or "0"
    return f"-{unsigned}" if sign else unsigned
