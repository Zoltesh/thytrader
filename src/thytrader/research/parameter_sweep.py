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
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
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
_INTEGER_PARAMETERS = frozenset({"period", "fast_period", "slow_period", "signal_period"})
_DECIMAL_PARAMETERS = frozenset({"stdev_multiplier", "value"})
ALLOWED_PARAMETERS = _INTEGER_PARAMETERS | _DECIMAL_PARAMETERS
_SWEEP_TAG = "research-sweep-candidate"


class SelectionMetric(StrEnum):
    """Fail-closed in-sample ranking metrics for sweeps and WFO."""

    TOTAL_RETURN_FRACTION = "total_return_fraction"
    TOTAL_NET_PNL = "total_net_pnl"
    MAXIMUM_DRAWDOWN_FRACTION = "maximum_drawdown_fraction"


class ParameterAxis(BaseModel):
    """One indicator parameter and the discrete values it may take."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    indicator_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    parameter: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    values: tuple[str, ...] = Field(min_length=2, max_length=_MAX_VALUES_PER_AXIS)

    @field_validator("parameter")
    @classmethod
    def require_allowed_parameter(cls, value: str) -> str:
        """Reject parameter names the canonical indicator models do not declare."""
        if value not in ALLOWED_PARAMETERS:
            raise ValueError(
                "parameter_axes.parameter must be period, fast_period, slow_period, "
                "signal_period, stdev_multiplier, or value"
            )
        return value

    @field_validator("values")
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep the Cartesian grid fail-closed and reproducible."""
        if len(value) != len(set(value)):
            raise ValueError("parameter_axes.values must be unique")
        return value


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


def expand_parameter_grid(
    axes: tuple[ParameterAxis, ...],
) -> tuple[tuple[tuple[str, str, str], ...], ...]:
    """Return Cartesian cells as (indicator_id, parameter, value) tuples in axis order."""
    if not axes:
        raise ValueError("parameter_axes is required")
    if len(axes) > _MAX_AXES:
        raise ValueError("parameter_axes accepts at most 4 axes")
    keys = [(axis.indicator_id, axis.parameter) for axis in axes]
    if len(keys) != len(set(keys)):
        raise ValueError("parameter_axes must use distinct indicator_id and parameter pairs")
    size = 1
    for axis in axes:
        size *= len(axis.values)
        if size > MAX_CANDIDATES:
            raise ValueError("parameter_axes Cartesian product must be at most 8 candidates")
    return tuple(
        tuple(
            (axes[index].indicator_id, axes[index].parameter, value)
            for index, value in enumerate(combo)
        )
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
    cell: tuple[tuple[str, str, str], ...],
    *,
    base_fingerprint: str,
) -> StrategyDefinition:
    """Substitute one grid cell and re-validate the canonical strategy document."""
    payload = base.model_dump(mode="python")
    for indicator_id, parameter, raw_value in cell:
        _assign_indicator_parameter(payload, indicator_id, parameter, raw_value)
    payload["strategy_id"] = _deterministic_uuid7(base.created_at, base_fingerprint, cell)
    payload["version"] = 1
    payload["status"] = StrategyStatus.PUBLISHED
    payload["name"] = _derived_name(base.name, cell)
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
        drawdown_fraction = Decimal(0) if self.peak == 0 else self.max_drawdown / self.peak
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
            maximum_drawdown_fraction=_canonical_decimal(drawdown_fraction),
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


def _derived_name(base_name: str, cell: tuple[tuple[str, str, str], ...]) -> str:
    """Build a bounded display name that still fits the schema."""
    summary = ",".join(f"{indicator}.{parameter}={value}" for indicator, parameter, value in cell)
    name = f"{base_name} [{summary}]"
    if len(name) <= 120:
        return name
    return f"{base_name} [sweep]"[:120]


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
