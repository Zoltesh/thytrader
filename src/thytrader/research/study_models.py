"""Typed request, plan, and result documents of research studies (ADR 0044).

Study kinds and window roles, execution budgets, the frozen pydantic documents an
agent submits (``ResearchStudyRequest``) and reads back (plans, studies, summaries),
and the request's kind-specific field rules. Planning, submission, aggregation, and
fingerprints live in :mod:`thytrader.research.studies`, which re-exports every name
here; this module never imports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.backtest.models import BacktestSummary
from thytrader.market_data.models import DatasetTimeframe
from thytrader.research.models import (
    EvaluationWindow,
    IndicatorTimeframeDataset,
    ReferenceInstrumentDataset,
    reject_removed_engine_selection,
)
from thytrader.research.parameter_sweep import (
    MAX_CANDIDATES,
    MAX_SYNC_CANDIDATES,
    AxisValue,
    ParameterAxis,
    SelectionMetric,
    StitchedOosEquity,
    validate_parameter_axes_candidate_budget,
)
from thytrader.research.stress import ExecutionStress

STUDY_CONTRACT_VERSION = "thytrader-research-study-v1"

_MAX_MARKETS = 8
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


class StudyKind(StrEnum):
    """Fail-closed research-study kinds for Phase 11 and ADR 0044."""

    OOS_HOLDOUT = "oos_holdout"
    WALK_FORWARD = "walk_forward"
    CROSS_MARKET = "cross_market"
    PARAMETER_SWEEP = "parameter_sweep"
    WALK_FORWARD_OPTIMIZATION = "walk_forward_optimization"


class FoldMode(StrEnum):
    """Walk-forward IS window motion."""

    ROLLING = "rolling"
    ANCHORED = "anchored"


class WindowRole(StrEnum):
    """How one child backtest participates in the study."""

    IN_SAMPLE = "in_sample"
    OUT_OF_SAMPLE = "out_of_sample"
    FULL_WINDOW = "full_window"
    SWEEP_CANDIDATE = "sweep_candidate"


@dataclass(frozen=True, slots=True)
class StudyBudget:
    """Child-work limits for one way of running a study.

    A synchronous submit (HTTP 201) runs every child inside the request, so it stays
    small. An async job (HTTP 202) runs in the research worker and may search a larger
    grid, still bounded by ``max_windows`` child backtests.
    """

    mode: Literal["sync", "async"]
    max_candidates: int
    max_windows: int


SYNC_STUDY_BUDGET = StudyBudget(mode="sync", max_candidates=MAX_SYNC_CANDIDATES, max_windows=128)
ASYNC_STUDY_BUDGET = StudyBudget(mode="async", max_candidates=MAX_CANDIDATES, max_windows=512)


class StudyFailedPhase(StrEnum):
    """Bounded submission phases a study failure can name."""

    PLAN = "plan"
    PUBLISH_DERIVED = "publish_derived"
    SUBMIT_CHILDREN = "submit_children"
    PERSIST_STUDY = "persist_study"
    UNKNOWN = "unknown"


class _FrozenStudyModel(BaseModel):
    """Reject unknown fields and prevent mutation of study documents."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketBinding(_FrozenStudyModel):
    """One published single-instrument strategy bound to a verified dataset.

    ``reference_dataset_fingerprints`` binds the strategy's read-only reference
    instruments (ADR 0096); a re-targeted market variant keeps the same references.
    """

    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    dataset_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )


class ResearchStudyRequest(_FrozenStudyModel):
    """Typed study assumptions. Does not retune strategy parameters."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    evaluation_start: datetime
    evaluation_end: datetime
    initial_quote_balance: str
    maker_fee_rate: str
    taker_fee_rate: str
    fixed_slippage_bps: str
    spread_bps: str | None = None
    execution_stress: ExecutionStress | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    oos_fraction: str | None = None
    embargo_bars: int = Field(default=0, ge=0, le=10_000)
    in_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    out_of_sample_bars: int | None = Field(default=None, ge=1, le=100_000)
    step_bars: int | None = Field(default=None, ge=1, le=100_000)
    fold_mode: FoldMode = FoldMode.ROLLING
    markets: tuple[MarketBinding, ...] | None = None
    candidate_strategy_fingerprints: tuple[str, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    parameter_axes: tuple[ParameterAxis, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
        description=(
            "1-4 axes with 2-8 values each. The Cartesian product must be at most 64 "
            "candidates, and at most 8 for a synchronous submit (not 8 per axis)."
        ),
    )
    selection_metric: SelectionMetric = Field(
        default=SelectionMetric.TOTAL_RETURN_FRACTION,
        exclude_if=lambda value: value is SelectionMetric.TOTAL_RETURN_FRACTION,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_engine_selection(cls, data: object) -> object:
        """Reject the retired engine selector with an explicit migration message."""
        return reject_removed_engine_selection(data)

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize study bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @field_validator("oos_fraction")
    @classmethod
    def validate_oos_fraction_text(cls, value: str | None) -> str | None:
        """Reject non-decimal OOS fractions before kind-specific bounds."""
        if value is None:
            return value
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("oos_fraction must be a plain decimal string") from error
        if parsed <= 0 or parsed >= 1:
            raise ValueError("oos_fraction must be greater than 0 and less than 1")
        return value

    @model_validator(mode="after")
    def validate_kind_fields(self) -> Self:
        """Require kind-specific fields and reject combinations that mix study types."""
        EvaluationWindow(starts_at=self.evaluation_start, ends_at=self.evaluation_end)
        if self.kind is StudyKind.CROSS_MARKET:
            _require_cross_market(self)
            return self
        _require_single_market(self)
        if self.kind is StudyKind.OOS_HOLDOUT:
            _require_oos_holdout(self)
            return self
        if self.kind is StudyKind.PARAMETER_SWEEP:
            _require_parameter_sweep(self)
            return self
        if self.kind is StudyKind.WALK_FORWARD_OPTIMIZATION:
            _require_walk_forward_optimization(self)
            return self
        _require_walk_forward(self)
        return self


class PlannedStudyWindow(_FrozenStudyModel):
    """One child evaluation window before submission."""

    label: str
    role: WindowRole
    fold_index: int = Field(ge=0)
    product_id: str
    timeframe: DatasetTimeframe
    strategy_fingerprint: str
    dataset_fingerprint: str
    htf_dataset_fingerprint: str | None = None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    reference_dataset_fingerprints: tuple[ReferenceInstrumentDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    evaluation_start: datetime
    evaluation_end: datetime

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize planned bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ResearchStudyPlan(_FrozenStudyModel):
    """Deterministic window schedule without simulation."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    request_fingerprint: str
    plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    timeframe: DatasetTimeframe
    windows: tuple[PlannedStudyWindow, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()


class ResearchStudyPlanSummary(_FrozenStudyModel):
    """Agent-safe plan projection without every child window."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    kind: StudyKind
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    timeframe: DatasetTimeframe
    window_count: int = Field(ge=1)
    fold_count: int = Field(ge=1)
    warnings: tuple[str, ...] = ()


class StudyWindowResult(_FrozenStudyModel):
    """One submitted child backtest identity plus its immutable summary."""

    label: str
    role: WindowRole
    fold_index: int = Field(ge=0)
    product_id: str
    run_fingerprint: str
    result_fingerprint: str
    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    evaluation_start: datetime
    evaluation_end: datetime
    summary: BacktestSummary
    selected: bool = Field(default=False, exclude_if=lambda value: value is False)

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize child bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StudyAggregate(_FrozenStudyModel):
    """Disclosed scored-window statistics that are not a stitched equity curve.

    ``oos_*`` fields carry genuine out-of-sample windows only. Parameter sweeps
    score ``sweep_candidate`` full windows on one shared evaluation range; their
    aggregates use the ``candidate_*`` aliases so the naming cannot imply an
    out-of-sample claim (ADR 0044 aggregate honesty).
    """

    window_count: int = Field(ge=1)
    oos_window_count: int = Field(ge=0)
    oos_trade_count: int = Field(ge=0)
    oos_winning_trade_count: int = Field(ge=0)
    oos_win_rate: str | None = None
    mean_oos_return_fraction: str | None = None
    mean_oos_drawdown_fraction: str | None = None
    mean_is_return_fraction: str | None = None
    is_oos_return_gap: str | None = None
    candidate_window_count: int | None = Field(default=None, ge=0)
    candidate_trade_count: int | None = Field(default=None, ge=0)
    candidate_winning_trade_count: int | None = Field(default=None, ge=0)
    mean_candidate_return_fraction: str | None = None
    mean_candidate_drawdown_fraction: str | None = None


class ResearchStudy(_FrozenStudyModel):
    """Canonical derived study after child backtests exist."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    study_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    kind: StudyKind
    windows: tuple[StudyWindowResult, ...] = Field(min_length=1)
    aggregate: StudyAggregate
    warnings: tuple[str, ...] = ()
    selection_metric: SelectionMetric | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    stitched_oos_equity: StitchedOosEquity | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class StudyWindowPnl(_FrozenStudyModel):
    """One child-window PnL headline without equity curves or trades.

    ``axis_values`` is the candidate's sweep assignment (``{"fast.period": 20}``; ``{}``
    when the study has one candidate) and ``evaluation_start`` / ``evaluation_end`` are
    the child's bounds, so one ``show-study`` call explains every row (ADR 0094).
    """

    label: str
    role: WindowRole
    fold_index: int = Field(ge=0)
    product_id: str
    evaluation_start: datetime
    evaluation_end: datetime
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    axis_values: dict[str, AxisValue] = Field(default_factory=dict)
    total_net_pnl: str
    total_return_fraction: str
    trade_count: int = Field(ge=0)
    selected: bool = Field(default=False, exclude_if=lambda value: value is False)

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize window bounds with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StudyCandidateAggregate(_FrozenStudyModel):
    """Every window of one candidate summed, so robustness across the grid is visible.

    ``oos_*`` counts genuine out-of-sample windows only (every WFO fold scores every
    candidate out of sample, not just the selected path). ``full_window_*`` covers sweep
    candidates and cross-market legs: full-range windows that are not an out-of-sample
    claim (ADR 0044). ``selected_window_count`` counts this candidate's windows the study
    selected in-sample.
    """

    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    product_id: str
    axis_values: dict[str, AxisValue] = Field(default_factory=dict)
    window_count: int = Field(ge=1)
    selected_window_count: int = Field(ge=0)
    in_sample_window_count: int = Field(ge=0)
    in_sample_total_net_pnl: str | None = None
    oos_window_count: int = Field(ge=0)
    oos_total_net_pnl: str | None = None
    oos_positive_window_count: int = Field(ge=0)
    oos_trade_count: int = Field(ge=0)
    full_window_count: int = Field(ge=0)
    full_window_total_net_pnl: str | None = None


class ResearchStudySummary(_FrozenStudyModel):
    """Agent-safe study projection without child windows or stitch point series."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    study_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    kind: StudyKind
    aggregate: StudyAggregate
    warnings: tuple[str, ...] = ()
    selection_metric: SelectionMetric | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    stitched_oos_equity: StitchedOosEquity | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    stitched_oos_points_downsampled: bool = False
    window_count: int = Field(ge=1)
    window_pnl: tuple[StudyWindowPnl, ...] = ()
    candidates: tuple[StudyCandidateAggregate, ...] = ()


def _require_single_market(request: ResearchStudyRequest) -> None:
    """Reject cross-market fields on single-instrument studies."""
    if request.markets is not None:
        raise ValueError("markets is only valid for cross_market studies")
    if request.strategy_fingerprint is None or request.dataset_fingerprint is None:
        raise ValueError("strategy_fingerprint and dataset_fingerprint are required")


def _require_oos_holdout(request: ResearchStudyRequest) -> None:
    """Require a fraction in (0, 1) and forbid walk-forward bar counts."""
    if request.in_sample_bars is not None or request.out_of_sample_bars is not None:
        raise ValueError("walk-forward bar counts are not valid for oos_holdout")
    if request.step_bars is not None:
        raise ValueError("step_bars is not valid for oos_holdout")
    if request.oos_fraction is None:
        raise ValueError("oos_fraction is required for oos_holdout")
    fraction = Decimal(request.oos_fraction)
    if fraction <= 0 or fraction >= 1:
        raise ValueError("oos_fraction must be greater than 0 and less than 1")
    _forbid_candidate_fields(request, kind="oos_holdout")


def _require_walk_forward(request: ResearchStudyRequest) -> None:
    """Require IS, OOS, and step bar counts."""
    if request.oos_fraction is not None:
        raise ValueError("oos_fraction is not valid for walk_forward")
    if (
        request.in_sample_bars is None
        or request.out_of_sample_bars is None
        or request.step_bars is None
    ):
        raise ValueError("walk_forward requires in_sample_bars, out_of_sample_bars, and step_bars")
    _forbid_candidate_fields(request, kind="walk_forward")


def _require_parameter_sweep(request: ResearchStudyRequest) -> None:
    """Require a candidate grid and forbid walk-forward splits."""
    if request.oos_fraction is not None:
        raise ValueError("oos_fraction is not valid for parameter_sweep")
    if request.in_sample_bars is not None or request.out_of_sample_bars is not None:
        raise ValueError("walk-forward bar counts are not valid for parameter_sweep")
    if request.step_bars is not None:
        raise ValueError("step_bars is not valid for parameter_sweep")
    _require_candidate_source(request)


def _require_walk_forward_optimization(request: ResearchStudyRequest) -> None:
    """Require fold geometry plus a candidate grid."""
    if request.oos_fraction is not None:
        raise ValueError("oos_fraction is not valid for walk_forward_optimization")
    if (
        request.in_sample_bars is None
        or request.out_of_sample_bars is None
        or request.step_bars is None
    ):
        raise ValueError(
            "walk_forward_optimization requires in_sample_bars, out_of_sample_bars, and step_bars"
        )
    _require_candidate_source(request)


def _forbid_candidate_fields(request: ResearchStudyRequest, *, kind: str) -> None:
    """Reject sweep/WFO candidate fields on validation-only study kinds."""
    if request.candidate_strategy_fingerprints or request.parameter_axes:
        raise ValueError(f"candidate grids are not valid for {kind}")


def _require_candidate_source(request: ResearchStudyRequest) -> None:
    """Require fingerprints or axes, never both, and bound the grid."""
    has_candidates = bool(request.candidate_strategy_fingerprints)
    has_axes = bool(request.parameter_axes)
    if has_candidates == has_axes:
        raise ValueError(
            "This study kind requires candidate_strategy_fingerprints or parameter_axes, not both"
        )
    if has_candidates:
        count = len(request.candidate_strategy_fingerprints)
        if count < 2 or count > MAX_CANDIDATES:
            raise ValueError(
                f"candidate_strategy_fingerprints requires between 2 and {MAX_CANDIDATES} values"
            )
        if len(set(request.candidate_strategy_fingerprints)) != count:
            raise ValueError("candidate_strategy_fingerprints must be unique")
        for fingerprint in request.candidate_strategy_fingerprints:
            if len(fingerprint) != 71 or not fingerprint.startswith("sha256:"):
                raise ValueError("candidate_strategy_fingerprints must be sha256 fingerprints")
        return
    validate_parameter_axes_candidate_budget(request.parameter_axes)


def _require_cross_market(request: ResearchStudyRequest) -> None:
    """Require 2-8 unique product bindings and reject single-market fields."""
    if request.strategy_fingerprint is not None or request.dataset_fingerprint is not None:
        raise ValueError("cross_market studies name markets instead of a single strategy")
    if request.oos_fraction is not None or request.in_sample_bars is not None:
        raise ValueError("cross_market studies use the full evaluation window per market")
    if request.out_of_sample_bars is not None or request.step_bars is not None:
        raise ValueError("cross_market studies use the full evaluation window per market")
    if request.markets is None or len(request.markets) < 2 or len(request.markets) > _MAX_MARKETS:
        raise ValueError("cross_market requires between 2 and 8 markets")
    fingerprints = [item.strategy_fingerprint for item in request.markets]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("cross_market markets must use distinct strategy fingerprints")
    _forbid_candidate_fields(request, kind="cross_market")
