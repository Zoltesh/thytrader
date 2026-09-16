"""Parameter-axis expansion, WFO selection, and stitched OOS equity.

Research-only helpers. They do not grant paper or live authority and they do not
interpolate candles or look ahead from out-of-sample results into selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from hashlib import sha256
from itertools import product
import json
import re
from typing import TYPE_CHECKING, Self, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.strategies.models import (
    StrategyDefinition,
    StrategyMetadata,
    StrategyStatus,
    decision_clock_indicators,
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    strategy_fingerprint,
)
from thytrader.strategies.publication import PublishedStrategy

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult, BacktestSummary

_MAX_AXES = 4
_MAX_VALUES_PER_AXIS = 8
MAX_CANDIDATES = 8
MAX_STITCHED_POINTS = 4096
_INDICATOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_INTEGER_PARAMETERS = frozenset(
    {
        "period",
        "fast_period",
        "slow_period",
        "signal_period",
        "k_period",
        "d_period",
        "max_bars_held",
        "max_entry_wait_bars",
    }
)
_INDICATOR_PARAMETERS = frozenset(
    {
        "period",
        "fast_period",
        "slow_period",
        "signal_period",
        "k_period",
        "d_period",
        "stdev_multiplier",
        "value",
    }
)
_SIZING_PARAMETERS = frozenset({"risk_fraction", "min_quote_notional", "max_quote_notional"})
_EXITS_PARAMETERS = frozenset(
    {
        "initial_stop_multiple",
        "take_profit_multiple",
        "trailing_stop_multiple",
        "max_bars_held",
    }
)
_EXECUTION_PARAMETERS = frozenset({"max_entry_wait_bars"})
_LITERAL_PARAMETERS = frozenset({"literal"})
_CONDITION_OPERATORS = frozenset(
    {
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "equals",
    }
)
ALLOWED_PARAMETERS = _INDICATOR_PARAMETERS
_SWEEP_TAG = "research-sweep-candidate"


class SweepAxisTarget(StrEnum):
    """Where one sweep axis writes. Product and timeframe are not sweepable."""

    INDICATOR = "indicator"
    SIZING = "sizing"
    EXITS = "exits"
    EXECUTION = "execution"
    ENTRY_LITERAL = "entry_literal"
    HTF_LITERAL = "htf_literal"


class SelectionMetric(StrEnum):
    """Fail-closed in-sample ranking metrics for sweeps and WFO."""

    TOTAL_RETURN_FRACTION = "total_return_fraction"
    TOTAL_NET_PNL = "total_net_pnl"
    MAXIMUM_DRAWDOWN_FRACTION = "maximum_drawdown_fraction"


class ParameterAxis(BaseModel):
    """One discrete sweep axis. Default target is indicator (omitted from JSON)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: SweepAxisTarget = Field(
        default=SweepAxisTarget.INDICATOR,
        exclude_if=lambda value: value is SweepAxisTarget.INDICATOR,
    )
    indicator_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    parameter: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    values: tuple[str, ...] = Field(min_length=2, max_length=_MAX_VALUES_PER_AXIS)
    condition_operator: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("values")
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep the Cartesian grid fail-closed and reproducible."""
        if len(value) != len(set(value)):
            raise ValueError("parameter_axes.values must be unique")
        return value

    @model_validator(mode="after")
    def validate_target_shape(self) -> Self:
        """Require locator fields that match the axis target without rewriting logic."""
        self._require_parameter_for_target()
        self._require_locator_for_target()
        if self.condition_operator is not None:
            if self.target not in {SweepAxisTarget.ENTRY_LITERAL, SweepAxisTarget.HTF_LITERAL}:
                raise ValueError("condition_operator is only valid on literal sweep axes")
            if self.condition_operator not in _CONDITION_OPERATORS:
                raise ValueError("condition_operator must be a non-crossover comparison operator")
        return self

    def _require_parameter_for_target(self) -> None:
        """Reject parameter names the chosen target does not declare."""
        allowed = _parameters_for_target(self.target)
        if self.parameter not in allowed:
            raise ValueError(_parameter_error(self.target))

    def _require_locator_for_target(self) -> None:
        """Require indicator_id only when the target addresses an indicator or literal."""
        needs_indicator = self.target in {
            SweepAxisTarget.INDICATOR,
            SweepAxisTarget.ENTRY_LITERAL,
            SweepAxisTarget.HTF_LITERAL,
        }
        if needs_indicator:
            if self.indicator_id is None or not _INDICATOR_ID_PATTERN.fullmatch(self.indicator_id):
                raise ValueError("parameter_axes.indicator_id is required for this target")
            return
        if self.indicator_id is not None:
            raise ValueError("parameter_axes.indicator_id is not valid for this target")


@dataclass(frozen=True, slots=True)
class AxisCell:
    """One Cartesian assignment used to derive a candidate document."""

    target: SweepAxisTarget
    locator: str
    parameter: str
    value: str
    condition_operator: str | None = None

    def identity_tuple(self) -> tuple[str, str, str]:
        """Return the fingerprint cell. Indicator-only grids keep the ADR 0044 3-tuple."""
        if self.target is SweepAxisTarget.INDICATOR and self.condition_operator is None:
            return (self.locator, self.parameter, self.value)
        return (
            f"{self.target.value}:{self.locator}:{self.condition_operator or ''}",
            self.parameter,
            self.value,
        )


class StitchedEquityPoint(BaseModel):
    """One compounded mark-to-market point on a stitched OOS path."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    candle_starts_at: datetime
    equity: str
    fold_index: int = Field(ge=0)
    result_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize stitch marks with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StitchedOosEquity(BaseModel):
    """Derived sequential OOS equity. Not a fourth backtest engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    available: bool
    reason: str | None = None
    initial_equity: str | None = None
    final_equity: str | None = None
    total_return_fraction: str | None = None
    maximum_drawdown: str | None = None
    maximum_drawdown_fraction: str | None = None
    point_count: int = Field(default=0, ge=0)
    points: tuple[StitchedEquityPoint, ...] = ()


@dataclass(frozen=True, slots=True)
class StitchSourceWindow:
    """One scored OOS child whose equity curve may participate in a stitch."""

    fold_index: int
    evaluation_start: datetime
    evaluation_end: datetime
    result_fingerprint: str
    result: BacktestResult


def expand_parameter_grid(axes: tuple[ParameterAxis, ...]) -> tuple[tuple[AxisCell, ...], ...]:
    """Return Cartesian cells in axis declaration order."""
    if not axes:
        raise ValueError("parameter_axes is required")
    if len(axes) > _MAX_AXES:
        raise ValueError("parameter_axes accepts at most 4 axes")
    keys = [
        (axis.target, axis.indicator_id or "", axis.parameter, axis.condition_operator or "")
        for axis in axes
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("parameter_axes must use distinct target, locator, and parameter tuples")
    size = 1
    for axis in axes:
        size *= len(axis.values)
        if size > MAX_CANDIDATES:
            raise ValueError("parameter_axes Cartesian product must be at most 8 candidates")
    return tuple(
        tuple(_cell_for_axis(axes[index], value) for index, value in enumerate(combo))
        for combo in product(*(axis.values for axis in axes))
    )


def derive_parameter_candidates(
    base: StrategyDefinition,
    axes: tuple[ParameterAxis, ...],
    *,
    base_fingerprint: str,
) -> tuple[PublishedStrategy, ...]:
    """Build deterministic published-shaped variants for one parameter grid."""
    cells = expand_parameter_grid(axes)
    derived: list[PublishedStrategy] = []
    fingerprints: set[str] = set()
    for cell in cells:
        definition = apply_parameter_cell(base, cell, base_fingerprint=base_fingerprint)
        fingerprint = strategy_fingerprint(definition)
        if fingerprint in fingerprints:
            raise ValueError("parameter_axes produced duplicate strategy fingerprints")
        fingerprints.add(fingerprint)
        derived.append(PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition))
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
    identity = tuple(item.identity_tuple() for item in assignments)
    payload["strategy_id"] = _deterministic_uuid7(base.created_at, base_fingerprint, identity)
    payload["version"] = 1
    payload["status"] = StrategyStatus.PUBLISHED
    payload["name"] = _derived_name(base.name, assignments)
    payload["description"] = (
        f"Derived sweep candidate from {base_fingerprint}. Not a human-authored version."
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


def select_candidate_fingerprint(
    scored: tuple[tuple[str, str], ...],
    metric: SelectionMetric,
) -> str:
    """Pick one fingerprint from in-sample scores. Ties use lexicographic order."""
    if not scored:
        raise ValueError("selection requires at least one in-sample score")
    minimize = metric is SelectionMetric.MAXIMUM_DRAWDOWN_FRACTION

    def _key(item: tuple[str, str]) -> tuple[Decimal, str]:
        fingerprint, text = item
        value = Decimal(text)
        ordered = value if minimize else -value
        return (ordered, fingerprint)

    return min(scored, key=_key)[0]


def metric_value(summary: BacktestSummary, metric: SelectionMetric) -> str:
    """Read the canonical decimal string for one selection metric."""
    if metric is SelectionMetric.TOTAL_RETURN_FRACTION:
        return summary.total_return_fraction
    if metric is SelectionMetric.TOTAL_NET_PNL:
        return summary.total_net_pnl
    return summary.maximum_drawdown_fraction


def stitch_oos_equity(windows: tuple[StitchSourceWindow, ...]) -> StitchedOosEquity:
    """Compound non-overlapping OOS equity returns without interpolating gaps."""
    if len(windows) < 2:
        return StitchedOosEquity(
            available=False,
            reason="Stitched OOS equity needs at least two scored out-of-sample windows.",
        )
    ordered = tuple(sorted(windows, key=lambda item: (item.evaluation_start, item.fold_index)))
    overlap = _first_overlap(ordered)
    if overlap is not None:
        return StitchedOosEquity(available=False, reason=overlap)
    tracker = _StitchTracker(initial=Decimal(ordered[0].result.summary.initial_equity))
    for window in ordered:
        window_initial = Decimal(window.result.summary.initial_equity)
        if window_initial == 0:
            return StitchedOosEquity(
                available=False,
                reason="Stitched OOS equity cannot scale a window whose initial equity is 0.",
            )
        for point in window.result.equity_curve:
            scaled = tracker.capital * (Decimal(point.equity) / window_initial)
            tracker.maybe_append(
                timestamp=point.candle_starts_at,
                equity=scaled,
                fold_index=window.fold_index,
                result_fingerprint=window.result_fingerprint,
            )
        tracker.capital = tracker.capital * (
            Decimal(window.result.summary.final_equity) / window_initial
        )
        tracker.maybe_append(
            timestamp=window.evaluation_end,
            equity=tracker.capital,
            fold_index=window.fold_index,
            result_fingerprint=window.result_fingerprint,
        )
    return tracker.document()


@dataclass
class _StitchTracker:
    """Accumulate compounded OOS marks and peak-to-trough drawdown."""

    initial: Decimal
    capital: Decimal = field(init=False)
    peak: Decimal = field(init=False)
    max_drawdown: Decimal = field(default_factory=lambda: Decimal(0))
    max_drawdown_fraction: Decimal = field(default_factory=lambda: Decimal(0))
    last_timestamp: datetime | None = None
    points: list[StitchedEquityPoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Start capital and peak at the first window's initial equity."""
        self.capital = self.initial
        self.peak = self.initial

    def maybe_append(
        self,
        *,
        timestamp: datetime,
        equity: Decimal,
        fold_index: int,
        result_fingerprint: str,
    ) -> None:
        """Record one mark when it is strictly after the previous timestamp."""
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            return
        if equity > self.peak:
            self.peak = equity
        drawdown = self.peak - equity
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
        drawdown_fraction = Decimal(0) if self.peak == 0 else drawdown / self.peak
        if drawdown_fraction > self.max_drawdown_fraction:
            self.max_drawdown_fraction = drawdown_fraction
        self.points.append(
            StitchedEquityPoint(
                candle_starts_at=timestamp,
                equity=_canonical_decimal(equity),
                fold_index=fold_index,
                result_fingerprint=result_fingerprint,
            )
        )
        self.last_timestamp = timestamp

    def document(self) -> StitchedOosEquity:
        """Freeze the accumulated path, or explain why stitching is unavailable."""
        if not self.points:
            return StitchedOosEquity(
                available=False,
                reason="Stitched OOS equity has no mark-to-market points.",
            )
        final = self.capital
        published_points = tuple(self.points)
        if len(published_points) > MAX_STITCHED_POINTS:
            published_points = ()
        return_fraction = (
            _canonical_decimal((final - self.initial) / self.initial) if self.initial != 0 else "0"
        )
        return StitchedOosEquity(
            available=True,
            initial_equity=_canonical_decimal(self.initial),
            final_equity=_canonical_decimal(final),
            total_return_fraction=return_fraction,
            maximum_drawdown=_canonical_decimal(self.max_drawdown),
            maximum_drawdown_fraction=_canonical_decimal(self.max_drawdown_fraction),
            point_count=len(self.points),
            points=published_points,
        )


def unavailable_stitched_equity(reason: str) -> StitchedOosEquity:
    """Return an explicit unavailable stitch document."""
    return StitchedOosEquity(available=False, reason=reason)


def _first_overlap(windows: tuple[StitchSourceWindow, ...]) -> str | None:
    """Describe the first overlapping pair, if any."""
    previous = windows[0]
    for current in windows[1:]:
        if current.evaluation_start < previous.evaluation_end:
            return (
                "OOS windows overlap, so stitched equity is not a continuous path. "
                "Use equal-weight OOS aggregates instead."
            )
        previous = current
    return None


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
        extra_indicator_timeframe_warmup(decision),
    )
    htf = definition.htf_filter
    if htf is not None:
        needed = extra_indicator_timeframe_warmup(htf.indicators)
        groups = dict(extra_indicator_timeframe_groups(definition))
        shared = groups.get(htf.timeframe)
        if shared:
            needed = max(needed, extra_indicator_timeframe_warmup(shared))
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


def _cell_for_axis(axis: ParameterAxis, value: str) -> AxisCell:
    """Bind one axis value into a Cartesian cell."""
    return AxisCell(
        target=axis.target,
        locator=axis.indicator_id or "",
        parameter=axis.parameter,
        value=value,
        condition_operator=axis.condition_operator,
    )


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


def _parameters_for_target(target: SweepAxisTarget) -> frozenset[str]:
    """Return the fail-closed parameter names legal on one axis target."""
    if target is SweepAxisTarget.INDICATOR:
        return _INDICATOR_PARAMETERS
    if target is SweepAxisTarget.SIZING:
        return _SIZING_PARAMETERS
    if target is SweepAxisTarget.EXITS:
        return _EXITS_PARAMETERS
    if target is SweepAxisTarget.EXECUTION:
        return _EXECUTION_PARAMETERS
    return _LITERAL_PARAMETERS


def _parameter_error(target: SweepAxisTarget) -> str:
    """Explain which parameter names a target accepts."""
    names = ", ".join(sorted(_parameters_for_target(target)))
    return f"parameter_axes.parameter must be one of: {names}"


def _derived_metadata(metadata: StrategyMetadata) -> dict[str, object]:
    """Tag derived candidates without dropping existing unique annotations."""
    tags = list(metadata.tags)
    if _SWEEP_TAG not in tags and len(tags) < 20:
        tags.append(_SWEEP_TAG)
    return {"tags": tuple(tags), "notes": metadata.notes}


def _deterministic_uuid7(
    created_at: datetime,
    base_fingerprint: str,
    cell: tuple[tuple[str, str, str], ...],
) -> UUID:
    """Mint a UUIDv7 whose timestamp is the base created_at and whose random bits are the cell."""
    payload = json.dumps(
        {"base": base_fingerprint, "cell": cell},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = sha256(payload.encode()).digest()
    milliseconds = int(created_at.timestamp() * 1_000)
    rand_a = int.from_bytes(digest[:2], "big") & 0x0FFF
    rand_b = int.from_bytes(digest[2:10], "big") & ((1 << 62) - 1)
    value = (milliseconds << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)


def _canonical_decimal(value: Decimal) -> str:
    """Render a finite Decimal as a plain string without scientific notation."""
    quantized = value.quantize(Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
