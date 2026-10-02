"""The descriptive indicator registry agrees with the schema, the report, and the web build."""

from __future__ import annotations

import ast
from decimal import Decimal
from itertools import product
from math import floor, isqrt
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError
import pytest

from thytrader.operator.indicator_report import (
    indicator_catalog_entries,
    web_indicator_warmup_examples_document,
)
from thytrader.strategies.indicator_catalog import (
    INDICATOR_KIND_SPECS,
    OHLCV_FIELDS,
    IndicatorKindSpec,
    IndicatorParameterSpec,
    default_indicator_definition,
    default_parameters,
    indicator_kind_spec,
)
from thytrader.strategies.models import (
    IndicatorDefinition,
    IndicatorKind,
    indicator_min_warmup,
    indicator_output_series,
)
from thytrader.web_catalog import WEB_GENERATED_DIRECTORY, render_web_catalog_files

if TYPE_CHECKING:
    from collections.abc import Callable

_ROOT = Path(__file__).resolve().parents[2]
_HISTORICAL_ORDER = (
    "ema",
    "sma",
    "rsi",
    "atr",
    "volume_sma",
    "highest",
    "lowest",
    "stdev",
    "stdev_sample",
    "roc",
    "williams_r",
    "cci",
    "wma",
    "momentum",
    "mfi",
    "macd",
    "bollinger",
    "stochastic",
    "adx",
    "identity",
    "constant",
)


def test_every_schema_kind_has_exactly_one_registry_spec() -> None:
    """The registry covers the enum, with the historical 21 first in report order."""
    kinds = [spec.kind for spec in INDICATOR_KIND_SPECS]
    assert len(kinds) == len(set(kinds)) == len(IndicatorKind)
    assert set(kinds) == set(IndicatorKind)
    assert tuple(kind.value for kind in kinds[:21]) == _HISTORICAL_ORDER


def test_generated_web_catalog_files_match_the_registry() -> None:
    """Regenerate with `uv run python scripts/export_indicator_catalog.py` on drift."""
    directory = _ROOT.joinpath(*WEB_GENERATED_DIRECTORY)
    for name, content in render_web_catalog_files().items():
        path = directory / name
        assert path.is_file(), f"missing {path}; run scripts/export_indicator_catalog.py"
        assert path.read_text(encoding="utf-8") == content, (
            f"{path} is stale; run `uv run python scripts/export_indicator_catalog.py`"
        )


def _candidates(parameter: IndicatorParameterSpec) -> list[int | str]:
    """Return boundary and default values to combine when searching for valid documents."""
    values: list[int | str] = []
    for value in (parameter.minimum, parameter.default, parameter.maximum):
        if value is None:
            continue
        if parameter.exclusive_minimum and value == parameter.minimum:
            continue
        if value not in values:
            values.append(value)
    if parameter.exclusive_minimum and isinstance(parameter.minimum, str):
        values.append(str(Decimal(parameter.minimum) + Decimal("0.001")))
    return values


def _valid(spec: IndicatorKindSpec, parameters: dict[str, int | str]) -> bool:
    """Return whether one parameter assignment validates for the kind."""
    payload: dict[str, object] = {"id": "probe", "kind": spec.kind.value, "parameters": parameters}
    if spec.default_input is not None:
        payload["input"] = spec.default_input
    try:
        IndicatorDefinition.model_validate(payload)
    except ValidationError:
        return False
    return True


def _some_assignment_validates(spec: IndicatorKindSpec, name: str, value: int | str) -> bool:
    """Search other parameters' boundary/default values for one valid combination."""
    others = [
        parameter
        for parameter in spec.parameters
        if parameter.name != name and not parameter.optional
    ]
    for combination in product(*(_candidates(parameter) for parameter in others)):
        parameters = {
            parameter.name: chosen for parameter, chosen in zip(others, combination, strict=True)
        }
        parameters[name] = value
        if _valid(spec, parameters):
            return True
    return False


def _just_outside(parameter: IndicatorParameterSpec, *, below: bool) -> int | str | None:
    """Return the closest value past one bound, or None when unbounded."""
    bound = parameter.minimum if below else parameter.maximum
    if bound is None:
        return None
    if isinstance(bound, int):
        return bound - 1 if below else bound + 1
    if below and parameter.exclusive_minimum:
        return bound
    step = Decimal("0.001")
    return str(Decimal(bound) - step) if below else str(Decimal(bound) + step)


_PARAMETER_CASES = [
    (spec, parameter)
    for spec in INDICATOR_KIND_SPECS
    for parameter in spec.parameters
    if parameter.minimum is not None or parameter.maximum is not None
]


@pytest.mark.parametrize(
    ("spec", "parameter"),
    _PARAMETER_CASES,
    ids=[f"{spec.kind.value}.{parameter.name}" for spec, parameter in _PARAMETER_CASES],
)
def test_registry_bounds_are_exactly_the_schema_bounds(
    spec: IndicatorKindSpec, parameter: IndicatorParameterSpec
) -> None:
    """Each advertised bound is reachable and the value just past it never validates."""
    for bound in (parameter.minimum, parameter.maximum):
        if bound is None or (parameter.exclusive_minimum and bound == parameter.minimum):
            continue
        assert _some_assignment_validates(spec, parameter.name, bound), bound
    for below in (True, False):
        outside = _just_outside(parameter, below=below)
        if outside is None:
            continue
        assert not _some_assignment_validates(spec, parameter.name, outside), outside


@pytest.mark.parametrize("spec", INDICATOR_KIND_SPECS, ids=lambda spec: spec.kind.value)
def test_defaults_inputs_and_outputs_match_the_schema(spec: IndicatorKindSpec) -> None:
    """Defaults validate, the input policy is enforced, and outputs are the engine keys."""
    definition = default_indicator_definition(spec, "probe")
    assert spec.outputs == (indicator_output_series(spec.kind) or ())
    assert definition.parameters.model_dump(mode="json") == default_parameters(spec)
    if spec.input_mode == "configurable":
        assert spec.inputs == OHLCV_FIELDS
        for field in OHLCV_FIELDS:
            payload = definition.model_dump(mode="json") | {"input": field}
            assert IndicatorDefinition.model_validate(payload).input == field
    elif spec.input_mode == "locked":
        assert definition.input == (spec.inputs[0] if len(spec.inputs) == 1 else spec.inputs)
        wrong = "open" if spec.inputs != ("open",) else "close"
        with pytest.raises(ValidationError):
            IndicatorDefinition.model_validate(
                definition.model_dump(mode="json") | {"input": wrong}
            )
    else:
        assert definition.input is None


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[int, int], int]] = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
}
_CALLS: dict[str, Callable[[list[int]], int]] = {
    "max": max,
    "floor": lambda arguments: floor(arguments[0]),
    "sqrt": lambda arguments: isqrt(arguments[0]),
}


def _evaluate_formula(text: str, variables: dict[str, int]) -> int:
    """Evaluate a documented warmup formula with a whitelisted integer AST walk."""

    def walk(node: ast.AST) -> int:
        """Evaluate one whitelisted expression node."""
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            return variables[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            return _BINARY_OPERATORS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return _CALLS[node.func.id]([walk(argument) for argument in node.args])
        raise AssertionError(f"formula node {ast.dump(node)} is not allowed")

    return walk(ast.parse(text, mode="eval"))


def test_documented_warmup_formulas_match_the_schema_examples() -> None:
    """The report's warmup text computes the same bars as indicator_min_warmup."""
    examples = web_indicator_warmup_examples_document().warmup_examples
    for spec in INDICATOR_KIND_SPECS:
        cases = examples[spec.kind.value]
        assert cases
        for case in cases:
            parameters = {
                name: value for name, value in case.parameters.items() if isinstance(value, int)
            }
            expected = _evaluate_formula(spec.warmup, parameters) + case.offset
            assert expected == case.warmup_bars, (spec.kind.value, case)


def test_report_rows_render_the_registry_and_keep_historical_fields() -> None:
    """The operator report lists every kind with legacy and new fields."""
    entries = {entry.kind: entry for entry in indicator_catalog_entries()}
    assert len(entries) == 53
    assert (entries["rsi"].period_min, entries["rsi"].period_max) == (2, 100)
    assert (entries["macd"].period_min, entries["macd"].period_max) == (2, 500)
    assert entries["macd"].parameter_kind == "macd"
    assert entries["ppo"].parameter_kind == "macd"
    assert entries["identity"].period_min is None
    assert entries["constant"].supports_timeframe is False
    assert entries["constant"].supports_offset is False
    assert entries["historical_volatility"].period_max == 500
    for kind, entry in entries.items():
        spec = indicator_kind_spec(IndicatorKind(kind))
        assert entry.default_warmup_bars == indicator_min_warmup(
            default_indicator_definition(spec, "probe")
        )
        assert entry.label == spec.label
        assert entry.category == spec.category.value
