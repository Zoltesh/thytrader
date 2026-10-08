"""Derive parameter-sweep candidate strategy documents from an axis grid.

Each grid cell is written onto a dumped copy of the base strategy (indicator,
sizing, exits, execution, or comparison-literal fields), then re-validated with
warmup covering the substituted periods. Research-only: candidates grant no paper or
live authority. Axes live in :mod:`~thytrader.research.sweep_axes`, selection in
:mod:`~thytrader.research.sweep_selection`, and stitched OOS equity in
:mod:`~thytrader.research.stitched_equity`.
"""

from __future__ import annotations

from typing import cast

from pydantic import ValidationError

from thytrader.research.sweep_axes import (
    _INTEGER_PARAMETERS,
    _OFFSET_PARAMETER,
    AxisCell,
    ParameterAxis,
    SweepAxisTarget,
    expand_parameter_grid,
)
from thytrader.strategies.models import (
    StrategyDefinition,
    StrategyMetadata,
    decision_clock_indicators,
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    strategy_fingerprint,
    strategy_indicator_operands,
)
from thytrader.strategies.snapshots import StrategySnapshot

_SWEEP_TAG = "research-sweep-candidate"


def derive_parameter_candidates(
    base: StrategyDefinition,
    axes: tuple[ParameterAxis, ...],
    *,
    base_fingerprint: str,
) -> tuple[StrategySnapshot, ...]:
    """Build deterministic published-shaped variants for one parameter grid."""
    cells = expand_parameter_grid(axes)
    derived: list[StrategySnapshot] = []
    fingerprints: set[str] = set()
    for cell in cells:
        definition = apply_parameter_cell(base, cell, base_fingerprint=base_fingerprint)
        fingerprint = strategy_fingerprint(definition)
        if fingerprint in fingerprints:
            raise ValueError("parameter_axes produced duplicate strategy fingerprints")
        fingerprints.add(fingerprint)
        derived.append(StrategySnapshot(strategy_fingerprint=fingerprint, definition=definition))
    return tuple(derived)


def apply_parameter_cell(
    base: StrategyDefinition,
    cell: tuple[AxisCell, ...] | tuple[tuple[str, str, str], ...],
    *,
    base_fingerprint: str,
) -> StrategyDefinition:
    """Substitute one grid cell and re-validate the canonical strategy document."""
    assignments = _normalize_cell(cell)
    payload = base.model_dump(mode="python")
    for assignment in assignments:
        _assign_axis_cell(payload, assignment)
    # Variants keep the base strategy_id so their snapshots belong to (and are
    # deleted with) the strategy they were swept from (ADR 0082).
    payload["name"] = _derived_name(base.name, assignments)
    payload["description"] = (
        f"Derived sweep candidate from {base_fingerprint}. Not a human-authored edit."
    )[:500]
    payload["metadata"] = _derived_metadata(base.metadata)
    _ensure_payload_warmup_covers_periods(payload)
    try:
        candidate = StrategyDefinition.model_validate(payload)
        return _cover_warmup(
            candidate,
            floor=base.data_requirements.warmup_bars,
            htf_floor=(
                base.htf_filter.data_requirements.warmup_bars if base.htf_filter is not None else 1
            ),
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError(str(error)) from error


def _assign_axis_cell(payload: dict[str, object], cell: AxisCell) -> None:
    """Write one typed assignment onto the dumped strategy document."""
    if cell.target is SweepAxisTarget.INDICATOR:
        _assign_indicator_parameter(payload, cell.locator, cell.parameter, cell.value)
        return
    if cell.target is SweepAxisTarget.SIZING:
        _assign_sizing_parameter(payload, cell.parameter, cell.value)
        return
    if cell.target is SweepAxisTarget.EXITS:
        _assign_exits_parameter(payload, cell.parameter, cell.value)
        return
    if cell.target is SweepAxisTarget.EXECUTION:
        _assign_execution_parameter(payload, cell.parameter, cell.value)
        return
    _assign_literal_parameter(payload, cell)


def _assign_indicator_parameter(
    payload: dict[str, object],
    indicator_id: str,
    parameter: str,
    raw_value: str,
) -> None:
    """Write one typed parameter onto the matching LTF or HTF indicator."""
    assigned = _assign_in_indicators(payload.get("indicators"), indicator_id, parameter, raw_value)
    htf = payload.get("htf_filter")
    if isinstance(htf, dict):
        assigned = (
            _assign_in_indicators(htf.get("indicators"), indicator_id, parameter, raw_value)
            or assigned
        )
    if not assigned:
        raise ValueError(f"parameter_axes indicator_id {indicator_id!r} is not on the strategy")


def _assign_in_indicators(
    indicators: object,
    indicator_id: str,
    parameter: str,
    raw_value: str,
) -> bool:
    """Mutate one indicator list in a dumped strategy payload."""
    if not isinstance(indicators, list | tuple):
        return False
    found = False
    parsed = _parse_parameter_value(parameter, raw_value)
    for item in indicators:
        if not isinstance(item, dict) or item.get("id") != indicator_id:
            continue
        if parameter == _OFFSET_PARAMETER:
            # The bar lag lives on the declaration (optional, so it may be absent).
            # StrategyDefinition re-validates after the write; constants reject it.
            declaration = cast("dict[str, object]", item)
            declaration[_OFFSET_PARAMETER] = parsed
            found = True
            continue
        parameters = item.get("parameters")
        if not isinstance(parameters, dict) or parameter not in parameters:
            raise ValueError(f"indicator {indicator_id!r} does not declare parameter {parameter!r}")
        # Dumped strategy documents are JSON-shaped; StrategyDefinition re-validates after write.
        writable = cast("dict[str, int | str]", parameters)
        writable[parameter] = parsed
        found = True
    return found


def _assign_sizing_parameter(payload: dict[str, object], parameter: str, raw_value: str) -> None:
    """Substitute one risk-fraction sizing field."""
    sizing = payload.get("sizing")
    if not isinstance(sizing, dict) or parameter not in sizing:
        raise ValueError(f"sizing does not declare parameter {parameter!r}")
    writable = cast("dict[str, int | str]", sizing)
    writable[parameter] = _parse_parameter_value(parameter, raw_value)


def _assign_exits_parameter(payload: dict[str, object], parameter: str, raw_value: str) -> None:
    """Substitute one initial-stop, take-profit, trailing, or time-exit field."""
    exits = payload.get("exits")
    if not isinstance(exits, dict):
        raise TypeError("exits are required for exits sweep axes")
    parsed = _parse_parameter_value(parameter, raw_value)
    if parameter == "initial_stop_multiple":
        _assign_nested_multiple(exits.get("initial_stop"), "initial_stop", parsed)
        return
    if parameter == "take_profit_multiple":
        _assign_nested_multiple(exits.get("take_profit"), "take_profit", parsed)
        return
    if parameter == "trailing_stop_multiple":
        _assign_trailing_multiple(exits.get("trailing_stop"), parsed)
        return
    time_exit = exits.get("time_exit")
    if not isinstance(time_exit, dict) or "max_bars_held" not in time_exit:
        raise ValueError("time_exit.max_bars_held is not on the strategy")
    writable = cast("dict[str, int | str]", time_exit)
    writable["max_bars_held"] = parsed


def _assign_nested_multiple(block: object, name: str, parsed: int | str) -> None:
    """Write `multiple` onto one dumped stop or take-profit object."""
    if not isinstance(block, dict) or "multiple" not in block:
        raise ValueError(f"{name}.multiple is not on the strategy")
    writable = cast("dict[str, int | str]", block)
    writable["multiple"] = parsed


def _assign_trailing_multiple(block: object, parsed: int | str) -> None:
    """Write ATR trailing multiple only when trailing is enabled."""
    if not isinstance(block, dict) or block.get("enabled") is not True:
        raise ValueError("trailing_stop_multiple requires an enabled ATR trailing stop")
    if "multiple" not in block:
        raise ValueError("trailing_stop.multiple is not on the strategy")
    writable = cast("dict[str, int | str]", block)
    writable["multiple"] = parsed


def _assign_execution_parameter(payload: dict[str, object], parameter: str, raw_value: str) -> None:
    """Substitute one execution-preference integer."""
    execution = payload.get("execution")
    if not isinstance(execution, dict) or parameter not in execution:
        raise ValueError(f"execution does not declare parameter {parameter!r}")
    writable = cast("dict[str, int | str]", execution)
    writable[parameter] = _parse_parameter_value(parameter, raw_value)


def _assign_literal_parameter(payload: dict[str, object], cell: AxisCell) -> None:
    """Substitute the unique matching comparison literal, or fail closed."""
    root = _literal_root(payload, cell.target)
    assigned = _assign_literals(
        root,
        cell.locator,
        cell.value,
        condition_operator=cell.condition_operator,
    )
    if assigned == 0:
        raise ValueError(
            f"{cell.target.value} has no unique literal comparison for indicator {cell.locator!r}"
        )
    if assigned > 1:
        raise ValueError(
            f"{cell.target.value} literal for {cell.locator!r} is ambiguous; "
            "set condition_operator or publish candidate fingerprints"
        )


def _literal_root(payload: dict[str, object], target: SweepAxisTarget) -> object:
    """Return the entry or HTF condition tree for a literal axis."""
    if target is SweepAxisTarget.ENTRY_LITERAL:
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise ValueError("entry.when is required for entry_literal axes")
        return entry.get("when")
    htf = payload.get("htf_filter")
    if not isinstance(htf, dict):
        raise TypeError("htf_filter is required for htf_literal axes")
    return htf.get("when")


def _assign_literals(
    node: object,
    indicator_id: str,
    raw_value: str,
    *,
    condition_operator: str | None,
) -> int:
    """Count and write matching comparison literals in a dumped condition tree."""
    if not isinstance(node, dict):
        return 0
    current = cast("dict[str, object]", node)
    assigned = 0
    if _comparison_matches(current, indicator_id, condition_operator):
        _write_literal(current, raw_value)
        assigned += 1
    for key in ("all", "any"):
        children = current.get(key)
        if isinstance(children, (list, tuple)):
            for child in children:
                assigned += _assign_literals(
                    child,
                    indicator_id,
                    raw_value,
                    condition_operator=condition_operator,
                )
    nested = current.get("not")
    if nested is not None:
        assigned += _assign_literals(
            nested,
            indicator_id,
            raw_value,
            condition_operator=condition_operator,
        )
    return assigned


def _comparison_matches(
    node: dict[str, object],
    indicator_id: str,
    condition_operator: str | None,
) -> bool:
    """True when this node compares the named indicator against a literal."""
    if "left" not in node or "right" not in node:
        return False
    if condition_operator is not None and node.get("operator") != condition_operator:
        return False
    left = node.get("left")
    right = node.get("right")
    return (_is_indicator_operand(left, indicator_id) and _is_literal_operand(right)) or (
        _is_indicator_operand(right, indicator_id) and _is_literal_operand(left)
    )


def _is_indicator_operand(operand: object, indicator_id: str) -> bool:
    """True when the dumped operand names the given indicator."""
    return isinstance(operand, dict) and operand.get("indicator") == indicator_id


def _is_literal_operand(operand: object) -> bool:
    """True when the dumped operand is a decimal literal."""
    return isinstance(operand, dict) and "literal" in operand


def _write_literal(node: dict[str, object], raw_value: str) -> None:
    """Replace the literal side of a comparison with the axis value."""
    parsed = _parse_parameter_value("literal", raw_value)
    for side in ("left", "right"):
        operand = node.get(side)
        if isinstance(operand, dict) and "literal" in operand:
            writable = cast("dict[str, int | str]", operand)
            writable["literal"] = parsed


def _parse_parameter_value(parameter: str, raw_value: str) -> int | str:
    """Parse one axis value as an int period or a canonical decimal string."""
    if parameter in _INTEGER_PARAMETERS:
        if not raw_value.isdigit() or (raw_value.startswith("0") and raw_value != "0"):
            raise ValueError(f"{parameter} values must be whole decimal integers without a sign")
        return int(raw_value)
    return raw_value


def _ensure_payload_warmup_covers_periods(payload: dict[str, object]) -> None:
    """Temporarily raise dumped warmup so substituted periods can re-validate."""
    _bump_warmup_field(payload.get("data_requirements"))
    htf = payload.get("htf_filter")
    if isinstance(htf, dict):
        _bump_warmup_field(htf.get("data_requirements"))


def _bump_warmup_field(requirements: object) -> None:
    """Set warmup_bars to the schema maximum when the field is present."""
    if not isinstance(requirements, dict) or not isinstance(requirements.get("warmup_bars"), int):
        return
    # Dumped strategy documents are JSON-shaped; StrategyDefinition re-validates after write.
    writable = cast("dict[str, int]", requirements)
    writable["warmup_bars"] = 10_000


def _cover_warmup(
    definition: StrategyDefinition,
    *,
    floor: int,
    htf_floor: int,
) -> StrategyDefinition:
    """Set warmup bars to the exact substituted-period requirement."""
    payload = definition.model_dump(mode="python")
    decision = decision_clock_indicators(definition)
    payload["data_requirements"]["warmup_bars"] = max(
        floor,
        extra_indicator_timeframe_warmup(
            decision, operands=strategy_indicator_operands(definition)
        ),
    )
    htf = definition.htf_filter
    if htf is not None:
        needed = extra_indicator_timeframe_warmup(
            htf.indicators, operands=strategy_indicator_operands(definition)
        )
        groups = dict(extra_indicator_timeframe_groups(definition))
        shared = groups.get(htf.timeframe)
        if shared:
            needed = max(
                needed,
                extra_indicator_timeframe_warmup(
                    shared, operands=strategy_indicator_operands(definition)
                ),
            )
        payload["htf_filter"]["data_requirements"]["warmup_bars"] = max(htf_floor, needed)
    return StrategyDefinition.model_validate(payload)


def _derived_name(base_name: str, cell: tuple[AxisCell, ...]) -> str:
    """Build a bounded display name that still fits the schema."""
    summary = ",".join(_cell_label(item) for item in cell)
    name = f"{base_name} [{summary}]"
    if len(name) <= 120:
        return name
    return f"{base_name} [sweep]"[:120]


def _cell_label(cell: AxisCell) -> str:
    """Render one axis assignment for the derived strategy name."""
    if cell.target is SweepAxisTarget.INDICATOR:
        return f"{cell.locator}.{cell.parameter}={cell.value}"
    if cell.locator:
        return f"{cell.target.value}.{cell.locator}.{cell.parameter}={cell.value}"
    return f"{cell.target.value}.{cell.parameter}={cell.value}"


def _normalize_cell(
    cell: tuple[AxisCell, ...] | tuple[tuple[str, str, str], ...],
) -> tuple[AxisCell, ...]:
    """Accept AxisCell grids or legacy indicator 3-tuples used by unit tests."""
    if not cell:
        raise ValueError("parameter_axes cell must not be empty")
    first = cell[0]
    if isinstance(first, AxisCell):
        return cast("tuple[AxisCell, ...]", cell)
    triples = cast("tuple[tuple[str, str, str], ...]", cell)
    return tuple(
        AxisCell(
            target=SweepAxisTarget.INDICATOR,
            locator=indicator_id,
            parameter=parameter,
            value=raw_value,
        )
        for indicator_id, parameter, raw_value in triples
    )


def _derived_metadata(metadata: StrategyMetadata) -> dict[str, object]:
    """Tag derived candidates without dropping existing unique annotations."""
    tags = list(metadata.tags)
    if _SWEEP_TAG not in tags and len(tags) < 20:
        tags.append(_SWEEP_TAG)
    return {"tags": tuple(tags), "notes": metadata.notes}
