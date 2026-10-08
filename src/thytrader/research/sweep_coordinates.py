"""Recover each sweep candidate's axis coordinates from its strategy definition."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from thytrader.research.sweep_axes import (
    _EXECUTION_PARAMETERS,
    _INDICATOR_PARAMETERS,
    _OFFSET_PARAMETER,
    _SIZING_PARAMETERS,
    AxisValue,
    SweepAxisTarget,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.strategies.models import StrategyDefinition


def candidate_axis_values(
    definitions: Mapping[str, StrategyDefinition],
) -> dict[str, dict[str, AxisValue]]:
    """Map each candidate fingerprint to the sweep coordinates that differ across candidates.

    Labels match derived candidate names (``fast.period``, ``sizing.risk_fraction``,
    ``exits.initial_stop_multiple``, ``entry_literal.rsi.literal``). The assignment is
    recovered from the candidates' own definitions, so a persisted study can name each
    candidate's axis values without the request that derived it. A single candidate, or
    candidates that differ only outside sweepable coordinates (another product), map to
    ``{}``.
    """
    coordinates = {
        fingerprint: sweep_coordinates(item) for fingerprint, item in definitions.items()
    }
    labels: dict[str, None] = {}
    for values in coordinates.values():
        labels.update(dict.fromkeys(values))
    varying = [
        label
        for label in labels
        if len({repr(values.get(label)) for values in coordinates.values()}) > 1
    ]
    return {
        fingerprint: {label: values[label] for label in varying if label in values}
        for fingerprint, values in coordinates.items()
    }


def sweep_coordinates(definition: StrategyDefinition) -> dict[str, AxisValue]:
    """Return every sweepable coordinate of one definition under its axis label."""
    payload = definition.model_dump(mode="json", by_alias=True)
    coordinates: dict[str, AxisValue] = {}
    _indicator_coordinates(payload.get("indicators"), coordinates)
    htf = payload.get("htf_filter")
    if isinstance(htf, dict):
        _indicator_coordinates(htf.get("indicators"), coordinates)
    _block_coordinates(payload.get("sizing"), _SIZING_PARAMETERS, "sizing", coordinates)
    _block_coordinates(payload.get("execution"), _EXECUTION_PARAMETERS, "execution", coordinates)
    _exits_coordinates(payload.get("exits"), coordinates)
    entry = payload.get("entry")
    if isinstance(entry, dict):
        _literal_coordinates(entry.get("when"), SweepAxisTarget.ENTRY_LITERAL, coordinates)
    if isinstance(htf, dict):
        _literal_coordinates(htf.get("when"), SweepAxisTarget.HTF_LITERAL, coordinates)
    return coordinates


def _axis_value(value: object) -> AxisValue | None:
    """Accept only document scalars an axis can write (never booleans or null)."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    return value


def _indicator_coordinates(indicators: object, coordinates: dict[str, AxisValue]) -> None:
    """Add ``<indicator id>.<parameter>`` (and ``.offset``) for every declared indicator."""
    if not isinstance(indicators, list):
        return
    for raw_item in cast("list[object]", indicators):
        item = _json_object(raw_item)
        identity = item.get("id")
        if not isinstance(identity, str):
            continue
        for name, raw in _json_object(item.get("parameters")).items():
            value = _axis_value(raw)
            if value is not None and name in _INDICATOR_PARAMETERS:
                coordinates[f"{identity}.{name}"] = value
        offset = _axis_value(item.get(_OFFSET_PARAMETER, 0))
        if offset is not None:
            coordinates[f"{identity}.{_OFFSET_PARAMETER}"] = offset


def _json_object(value: object) -> dict[str, object]:
    """Narrow one dumped JSON value to an object, or an empty one."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _block_coordinates(
    block: object, names: frozenset[str], prefix: str, coordinates: dict[str, AxisValue]
) -> None:
    """Add ``<prefix>.<name>`` for the sweepable fields one sizing/execution block declares."""
    if not isinstance(block, dict):
        return
    for name in sorted(names):
        value = _axis_value(block.get(name))
        if value is not None:
            coordinates[f"{prefix}.{name}"] = value


def _exits_coordinates(exits: object, coordinates: dict[str, AxisValue]) -> None:
    """Add the exits axes: stop, take-profit, and trailing multiples, and max bars held."""
    if not isinstance(exits, dict):
        return
    sources = (
        ("initial_stop_multiple", exits.get("initial_stop"), "multiple"),
        ("take_profit_multiple", exits.get("take_profit"), "multiple"),
        ("trailing_stop_multiple", exits.get("trailing_stop"), "multiple"),
        ("max_bars_held", exits.get("time_exit"), "max_bars_held"),
    )
    for name, block, key in sources:
        value = _axis_value(block.get(key)) if isinstance(block, dict) else None
        if value is not None:
            coordinates[f"exits.{name}"] = value


def _literal_coordinates(
    node: object, target: SweepAxisTarget, coordinates: dict[str, AxisValue]
) -> None:
    """Add ``<target>.<indicator>.literal`` for indicator-vs-literal comparisons.

    An indicator compared with several literals gets one label per operator
    (``entry_literal.rsi.greater_than.literal``) so the labels stay unique.
    """
    found: list[tuple[str, str, AxisValue]] = []
    _collect_literals(node, found)
    per_indicator: dict[str, int] = {}
    for indicator, _operator, _value in found:
        per_indicator[indicator] = per_indicator.get(indicator, 0) + 1
    for indicator, operator, value in found:
        middle = indicator if per_indicator[indicator] == 1 else f"{indicator}.{operator}"
        coordinates[f"{target.value}.{middle}.literal"] = value


def _collect_literals(node: object, found: list[tuple[str, str, AxisValue]]) -> None:
    """Depth-first (indicator, operator, literal) triples of one dumped condition tree."""
    if not isinstance(node, dict):
        return
    current = _json_object(node)
    left, right = _json_object(current.get("left")), _json_object(current.get("right"))
    for indicator_side, literal_side in ((left, right), (right, left)):
        indicator = indicator_side.get("indicator")
        if isinstance(indicator, str) and "literal" in literal_side:
            value = _axis_value(literal_side.get("literal"))
            if value is not None:
                found.append((indicator, str(current.get("operator")), value))
    for key in ("all", "any"):
        children = current.get(key)
        for child in cast("list[object]", children) if isinstance(children, list) else ():
            _collect_literals(child, found)
    _collect_literals(current.get("not"), found)
