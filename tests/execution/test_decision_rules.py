"""Leaf-by-leaf rule explanations share the evaluator's tri-state semantics (ADR 0087)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from thytrader.execution.decision_rules import (
    OperandLabeler as Labeler,
    condition_trace,
    count_unmet_leaves,
    display_decimal,
    first_unmet,
    indicator_label,
    met_text,
    unmet_text,
)
from thytrader.execution.decisions import (
    ConditionComparisonTrace,
    ConditionGroupTrace,
    ConditionResult,
)
from thytrader.strategies.indicator_catalog import (
    INDICATOR_KIND_SPECS,
    default_indicator_definition,
    indicator_kind_spec,
)
from thytrader.strategies.models import (
    AllCondition,
    AnyCondition,
    ComparisonCondition,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    NotCondition,
)


def _indicator(payload: dict[str, object]) -> IndicatorDefinition:
    """Validate one indicator definition."""
    return IndicatorDefinition.model_validate(payload)


_RSI = _indicator({"id": "rsi", "kind": "rsi", "input": "close", "parameters": {"period": 14}})
_FAST = _indicator({"id": "fast", "kind": "ema", "input": "close", "parameters": {"period": 20}})
_SLOW = _indicator({"id": "slow", "kind": "ema", "input": "close", "parameters": {"period": 50}})
_LABELER = Labeler((_RSI, _FAST, _SLOW), "1h")


def _comparison(payload: dict[str, object]) -> ComparisonCondition:
    """Validate one comparison leaf."""
    return ComparisonCondition.model_validate(payload)


_RSI_GTE_50 = _comparison(
    {"left": {"indicator": "rsi"}, "operator": "greater_than_or_equal", "right": {"literal": "50"}}
)
_FAST_CROSSES_SLOW = _comparison(
    {"left": {"indicator": "fast"}, "operator": "crosses_above", "right": {"indicator": "slow"}}
)


@pytest.mark.parametrize(
    ("payload", "series", "expected"),
    [
        (
            {"id": "rsi", "kind": "rsi", "input": "close", "parameters": {"period": 14}},
            None,
            "RSI(14)",
        ),
        (
            {"id": "e", "kind": "ema", "input": "high", "parameters": {"period": 9}},
            None,
            "EMA(9, high)",
        ),
        (
            {
                "id": "m",
                "kind": "macd",
                "input": "close",
                "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
            },
            "signal",
            "MACD(12,26,9) signal",
        ),
        (
            {
                "id": "b",
                "kind": "bollinger",
                "input": "close",
                "parameters": {"period": 20, "stdev_multiplier": "2"},
            },
            "upper",
            "BB(20,2) upper",
        ),
        (
            {
                "id": "s",
                "kind": "stochastic",
                "input": ["high", "low", "close"],
                "parameters": {"k_period": 14, "d_period": 3},
            },
            "k",
            "Stoch(14,3) %K",
        ),
        (
            {
                "id": "a",
                "kind": "adx",
                "input": ["high", "low", "close"],
                "parameters": {"period": 14},
            },
            "plus_di",
            "ADX(14) +DI",
        ),
        ({"id": "c", "kind": "identity", "input": "close", "parameters": {}}, None, "Close"),
        ({"id": "lvl", "kind": "constant", "parameters": {"value": "70"}}, None, "70"),
    ],
)
def test_indicator_labels_read_like_the_builder(
    payload: dict[str, object], series: str | None, expected: str
) -> None:
    """Labels name kind, parameters, non-default input, and output series."""
    assert indicator_label(_indicator(payload), series=series) == expected


@pytest.mark.parametrize(
    ("kind", "series", "expected"),
    [
        (IndicatorKind.SUPERTREND, "value", "Supertrend(10,3)"),
        (IndicatorKind.SUPERTREND, "direction", "Supertrend(10,3) direction"),
        (IndicatorKind.KELTNER, "upper", "Keltner(20,10,2) upper"),
        (IndicatorKind.ICHIMOKU, "senkou_a", "Ichimoku(9,26,52) Senkou A"),
        (IndicatorKind.PPO, "ppo", "PPO(12,26,9) line"),
        (IndicatorKind.VORTEX, "plus", "Vortex(14) +VI"),
        (IndicatorKind.STOCHASTIC_RSI, "k", "Stochastic RSI(14,14,3,3) %K"),
        (IndicatorKind.OBV, "obv", "OBV(20)"),
        (IndicatorKind.PARABOLIC_SAR, None, "Parabolic SAR(0.02,0.2)"),
        (IndicatorKind.HISTORICAL_VOLATILITY, None, "Historical volatility(20)"),
        (IndicatorKind.VOLUME_SMA, None, "Volume SMA(20)"),
        (IndicatorKind.HIGHEST, None, "Highest(20, high)"),
    ],
)
def test_catalog_kinds_use_registry_labels_and_parameter_order(
    kind: IndicatorKind, series: str | None, expected: str
) -> None:
    """Every catalog kind reads ``Label(parameters) series``; locked inputs stay implicit."""
    indicator = default_indicator_definition(indicator_kind_spec(kind), "x")
    assert indicator_label(indicator, series=series) == expected


def test_every_catalog_kind_and_series_has_a_label() -> None:
    """No kind falls back to its id or a raw enum/series token."""
    for spec in INDICATOR_KIND_SPECS:
        indicator = default_indicator_definition(spec, "x")
        for series in spec.outputs or (None,):
            text = indicator_label(indicator, series=series)
            assert "_" not in text
            assert text != "x"
            if spec.kind not in (IndicatorKind.IDENTITY, IndicatorKind.CONSTANT):
                assert "(" in text


def test_offset_indicators_read_bars_ago_on_their_own_clock() -> None:
    """A lagged breakout level reads ``Donchian(20) upper (1 bar ago)``."""
    donchian = default_indicator_definition(
        indicator_kind_spec(IndicatorKind.DONCHIAN), "don", offset=1
    )
    close = _indicator({"id": "close_now", "kind": "identity", "input": "close", "parameters": {}})
    rsi_lag = _indicator(
        {
            "id": "rsi_lag",
            "kind": "rsi",
            "input": "close",
            "parameters": {"period": 14},
            "timeframe": "4h",
            "offset": 3,
        }
    )
    labeler = Labeler((donchian, close, rsi_lag), "1h")
    leaf = _comparison(
        {
            "left": {"indicator": "close_now"},
            "operator": "greater_than",
            "right": {"indicator": "don", "series": "upper"},
        }
    )
    trace = condition_trace(
        leaf, {"close_now": Decimal("101"), "don.upper": Decimal("100.5")}, None, labeler=labeler
    )
    assert isinstance(trace, ConditionComparisonTrace)
    assert trace.label == "Close > Donchian(20) upper (1 bar ago)"
    assert trace.right.key == "don.upper"
    assert trace.result is ConditionResult.TRUE
    assert labeler.label(IndicatorOperand(indicator="rsi_lag")) == "RSI(14) [4h] (3 bars ago)"


def test_extra_timeframe_indicators_carry_their_clock() -> None:
    """An indicator bound to another clock is suffixed, e.g. ``RSI(14) [4h]``."""
    rsi_4h = _indicator(
        {
            "id": "rsi4",
            "kind": "rsi",
            "input": "close",
            "parameters": {"period": 14},
            "timeframe": "4h",
        }
    )
    leaf = _comparison(
        {"left": {"indicator": "rsi4"}, "operator": "less_than", "right": {"literal": "30"}}
    )
    trace = condition_trace(leaf, {"rsi4": Decimal("25")}, None, labeler=Labeler((rsi_4h,), "1h"))
    assert isinstance(trace, ConditionComparisonTrace)
    assert trace.left.label == "RSI(14) [4h]"
    assert trace.result is ConditionResult.TRUE


def test_false_leaf_reads_value_versus_threshold() -> None:
    """``RSI(14) 47.21 needs ≥ 50`` with exact values kept in the trace."""
    trace = condition_trace(_RSI_GTE_50, {"rsi": Decimal("47.2134")}, None, labeler=_LABELER)
    assert isinstance(trace, ConditionComparisonTrace)
    assert trace.left.value == "47.2134"
    assert trace.right.value == "50"
    assert trace.result is ConditionResult.FALSE
    assert unmet_text(trace) == "RSI(14) 47.21 needs ≥ 50"


def test_undefined_operand_is_unknown_never_false() -> None:
    """Missing values stay unknown (warmup), matching the evaluator."""
    trace = condition_trace(_RSI_GTE_50, {"rsi": None}, None, labeler=_LABELER)
    assert trace.result is ConditionResult.UNKNOWN
    assert unmet_text(trace) == "RSI(14) has no value yet"


def test_crossover_records_previous_and_current_values() -> None:
    """Crossovers keep both bars' values and explain a missed cross."""
    current = {"fast": Decimal("101.5"), "slow": Decimal("102")}
    previous = {"fast": Decimal("100"), "slow": Decimal("101")}
    trace = condition_trace(_FAST_CROSSES_SLOW, current, previous, labeler=_LABELER)
    assert isinstance(trace, ConditionComparisonTrace)
    assert (trace.left.previous_value, trace.left.value) == ("100", "101.5")
    assert (trace.right.previous_value, trace.right.value) == ("101", "102")
    assert trace.result is ConditionResult.FALSE
    assert unmet_text(trace) == "EMA(20) did not cross above EMA(50) (101.5 vs 102)"


def test_all_any_not_groups_keep_structure_and_find_the_deciding_leaf() -> None:
    """Groups carry their own result; the summary points at the first deciding leaf."""
    tree = AllCondition.model_validate(
        {
            "all": [
                _RSI_GTE_50.model_dump(by_alias=True),
                {
                    "any": [
                        _FAST_CROSSES_SLOW.model_dump(by_alias=True),
                        {"not": _RSI_GTE_50.model_dump(by_alias=True)},
                    ]
                },
            ]
        }
    )
    current = {"rsi": Decimal("55"), "fast": Decimal("99"), "slow": Decimal("100")}
    previous = {"rsi": Decimal("54"), "fast": Decimal("98"), "slow": Decimal("100")}
    trace = condition_trace(tree, current, previous, labeler=_LABELER)
    assert isinstance(trace, ConditionGroupTrace)
    assert trace.node == "all"
    assert trace.result is ConditionResult.FALSE
    any_group = trace.children[1]
    assert isinstance(any_group, ConditionGroupTrace)
    assert any_group.node == "any"
    not_group = any_group.children[1]
    assert isinstance(not_group, ConditionGroupTrace)
    assert (not_group.node, not_group.result) == ("not", ConditionResult.FALSE)
    deciding = first_unmet(trace)
    assert isinstance(deciding, ConditionComparisonTrace)
    assert deciding.label == "EMA(20) crosses above EMA(50)"
    assert count_unmet_leaves(trace) == 2
    assert unmet_text(not_group) == "RSI(14) 55 ≥ 50 must not hold"


def test_not_and_any_groups_match_evaluator_results() -> None:
    """NOT inverts a defined child; ANY is true when one child holds."""
    negated = NotCondition.model_validate({"not": _RSI_GTE_50.model_dump(by_alias=True)})
    either = AnyCondition.model_validate(
        {
            "any": [
                _RSI_GTE_50.model_dump(by_alias=True),
                _FAST_CROSSES_SLOW.model_dump(by_alias=True),
            ]
        }
    )
    values = {"rsi": Decimal("40"), "fast": Decimal("2"), "slow": Decimal("1")}
    previous = {"rsi": Decimal("40"), "fast": Decimal("1"), "slow": Decimal("1")}
    assert (
        condition_trace(negated, values, previous, labeler=_LABELER).result is ConditionResult.TRUE
    )
    held = condition_trace(either, values, previous, labeler=_LABELER)
    assert held.result is ConditionResult.TRUE
    assert met_text(held) == "EMA(20) 2 crosses above EMA(50) 1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("47.2134", "47.21"),
        ("64123.456", "64123.46"),
        ("0.000012345", "0.00001235"),
        ("50", "50"),
        ("0", "0"),
        ("-3.14159", "-3.142"),
        (None, "n/a"),
    ],
)
def test_display_decimal_rounds_for_summaries_only(text: str | None, expected: str) -> None:
    """Summaries show four significant digits; records keep exact strings."""
    assert display_decimal(text) == expected
