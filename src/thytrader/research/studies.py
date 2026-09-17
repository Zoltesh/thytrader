"""Walk-forward, OOS, cross-market, sweep, and WFO research study composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.backtest.models import (
    BacktestEngineContract,  # noqa: TC001 - Pydantic model field.
    BacktestResult,  # noqa: TC001 - used as a runtime result map value.
    BacktestSummary,  # noqa: TC001 - Pydantic model field.
)
from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
)
from thytrader.market_data.models import DatasetTimeframe, parse_candle_interval
from thytrader.research.catalog import (
    ResearchStudyCatalog,
    StudyCatalogSummary,
    StudyCatalogUnavailableError,
)
from thytrader.research.models import EvaluationWindow, IndicatorTimeframeDataset
from thytrader.research.parameter_sweep import (
    MAX_CANDIDATES,
    ParameterAxis,
    SelectionMetric,
    StitchedOosEquity,
    StitchSourceWindow,
    derive_parameter_candidates,
    metric_value,
    select_candidate_fingerprint,
    stitch_oos_equity,
    unavailable_stitched_equity,
    validate_parameter_axes_candidate_budget,
)
from thytrader.strategies.publication import StrategyPublicationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.backtest.submission import BacktestSubmitter
    from thytrader.persistence.backtest_results import BacktestResultReader
    from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationStore

STUDY_CONTRACT_VERSION = "thytrader-research-study-v1"
_FINGERPRINT_PREFIX = "sha256:"
_MAX_FOLDS = 24
_MAX_MARKETS = 8
_MAX_STUDY_WINDOWS = 128
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


class StudyPlanningError(ValueError):
    """Reject a study request that cannot form a valid window schedule."""


class ResearchStudyError(RuntimeError):
    """Report a redacted study failure without trading authority."""


class _FrozenStudyModel(BaseModel):
    """Reject unknown fields and prevent mutation of study documents."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketBinding(_FrozenStudyModel):
    """One published single-instrument strategy bound to a verified dataset."""

    strategy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    dataset_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
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
    engine_contract_version: BacktestEngineContract
    spread_bps: str | None = None
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    htf_dataset_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
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
            "1-4 axes with 2-8 values each. Cartesian product must be at most 8 candidates "
            "(not 8 per axis)."
        ),
    )
    selection_metric: SelectionMetric = Field(
        default=SelectionMetric.TOTAL_RETURN_FRACTION,
        exclude_if=lambda value: value is SelectionMetric.TOTAL_RETURN_FRACTION,
    )

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
    """Disclosed OOS statistics that are not a stitched equity curve."""

    window_count: int = Field(ge=1)
    oos_window_count: int = Field(ge=0)
    oos_trade_count: int = Field(ge=0)
    oos_winning_trade_count: int = Field(ge=0)
    oos_win_rate: str | None = None
    mean_oos_return_fraction: str | None = None
    mean_oos_drawdown_fraction: str | None = None
    mean_is_return_fraction: str | None = None
    is_oos_return_gap: str | None = None


class ResearchStudy(_FrozenStudyModel):
    """Canonical derived study after child backtests exist."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    study_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    kind: StudyKind
    engine_contract_version: BacktestEngineContract
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


class ResearchStudySummary(_FrozenStudyModel):
    """Agent-safe study projection without child windows or stitch point series."""

    schema_version: Literal["thytrader-research-study-v1"] = STUDY_CONTRACT_VERSION
    study_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    kind: StudyKind
    engine_contract_version: BacktestEngineContract
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
    window_count: int = Field(ge=1)


def summarize_research_study(study: ResearchStudy) -> ResearchStudySummary:
    """Project one persisted study into a bounded summary document."""
    stitched = study.stitched_oos_equity
    if stitched is not None and stitched.points:
        stitched = stitched.model_copy(update={"points": ()})
    return ResearchStudySummary(
        study_fingerprint=study.study_fingerprint,
        request_fingerprint=study.request_fingerprint,
        kind=study.kind,
        engine_contract_version=study.engine_contract_version,
        aggregate=study.aggregate,
        warnings=study.warnings,
        selection_metric=study.selection_metric,
        stitched_oos_equity=stitched,
        window_count=len(study.windows),
    )


def summarize_research_study_plan(plan: ResearchStudyPlan) -> ResearchStudyPlanSummary:
    """Project one planned study into a bounded summary without child windows."""
    fold_count = len({window.fold_index for window in plan.windows})
    return ResearchStudyPlanSummary(
        kind=plan.kind,
        request_fingerprint=plan.request_fingerprint,
        plan_fingerprint=plan.plan_fingerprint,
        timeframe=plan.timeframe,
        window_count=len(plan.windows),
        fold_count=fold_count,
        warnings=plan.warnings,
    )


def request_fingerprint(request: ResearchStudyRequest) -> str:
    """Return the SHA-256 identity of the canonical study request."""
    payload = request.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def plan_fingerprint(plan: ResearchStudyPlan) -> str:
    """Return the SHA-256 identity of the effective child window schedule."""
    windows = [window.model_dump(mode="json", exclude_none=True) for window in plan.windows]
    payload = {
        "kind": plan.kind.value,
        "timeframe": plan.timeframe,
        "windows": windows,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def study_fingerprint(study: ResearchStudy) -> str:
    """Return the SHA-256 identity of the derived study excluding its own fingerprint."""
    payload = study.model_dump(mode="json", exclude_none=True)
    payload.pop("study_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical.encode()).hexdigest()}"


def canonical_study_json(study: ResearchStudy) -> str:
    """Return the stored canonical study document including its fingerprint."""
    payload = study.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def study_catalog_summary(study: ResearchStudy, plan: ResearchStudyPlan) -> StudyCatalogSummary:
    """Build one catalog row from an assembled study and its window plan."""
    selected = next(
        (window.strategy_fingerprint for window in study.windows if window.selected),
        None,
    )
    stitched = None
    if study.stitched_oos_equity is not None:
        stitched = study.stitched_oos_equity.available
    return StudyCatalogSummary(
        study_fingerprint=study.study_fingerprint,
        request_fingerprint=study.request_fingerprint,
        plan_fingerprint=plan.plan_fingerprint,
        kind=study.kind.value,
        engine_contract_version=study.engine_contract_version,
        published_at=datetime.now(UTC),
        product_id=plan.windows[0].product_id,
        timeframe=plan.timeframe,
        window_count=study.aggregate.window_count,
        selected_strategy_fingerprint=selected,
        mean_oos_return_fraction=study.aggregate.mean_oos_return_fraction,
        stitched_oos_available=stitched,
        selection_metric=study.selection_metric,
    )


def plan_study(
    request: ResearchStudyRequest,
    *,
    publications: dict[str, PublishedStrategy],
) -> ResearchStudyPlan:
    """Build the child window schedule from published strategy metadata."""
    warnings: list[str] = []
    if request.kind is StudyKind.CROSS_MARKET:
        windows, timeframe = _plan_cross_market(request, publications)
    elif request.kind is StudyKind.PARAMETER_SWEEP:
        windows, timeframe = _plan_parameter_sweep(request, publications, warnings)
    elif request.kind is StudyKind.WALK_FORWARD_OPTIMIZATION:
        windows, timeframe = _plan_walk_forward_optimization(request, publications, warnings)
    else:
        windows, timeframe = _plan_single_market(request, publications, warnings)
    if not windows:
        raise StudyPlanningError("The evaluation window cannot form any study child windows.")
    if len(windows) > _MAX_STUDY_WINDOWS:
        raise StudyPlanningError("A research study may emit at most 128 child windows.")
    plan = ResearchStudyPlan(
        kind=request.kind,
        request_fingerprint=request_fingerprint(request),
        plan_fingerprint=_FINGERPRINT_PREFIX + ("0" * 64),
        timeframe=timeframe,
        windows=tuple(windows),
        warnings=tuple(warnings),
    )
    return plan.model_copy(update={"plan_fingerprint": plan_fingerprint(plan)})


def window_submission_request(
    request: ResearchStudyRequest,
    window: PlannedStudyWindow,
) -> BacktestSubmissionRequest:
    """Map one planned window onto the existing backtest submission contract."""
    return BacktestSubmissionRequest(
        strategy_fingerprint=window.strategy_fingerprint,
        dataset_fingerprint=window.dataset_fingerprint,
        htf_dataset_fingerprint=window.htf_dataset_fingerprint,
        indicator_dataset_fingerprints=window.indicator_dataset_fingerprints,
        evaluation_start=window.evaluation_start,
        evaluation_end=window.evaluation_end,
        initial_quote_balance=request.initial_quote_balance,
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
        engine_contract_version=request.engine_contract_version,
        spread_bps=request.spread_bps,
    )


def aggregate_windows(
    windows: tuple[StudyWindowResult, ...],
    *,
    selected_only: bool = False,
) -> StudyAggregate:
    """Summarize scored windows without treating stitching as a live fill."""
    pool = windows
    if selected_only:
        marked = tuple(item for item in windows if item.selected)
        if marked:
            pool = marked
    oos = tuple(item for item in pool if item.role is not WindowRole.IN_SAMPLE)
    is_windows = tuple(item for item in pool if item.role is WindowRole.IN_SAMPLE)
    oos_trades = sum(item.summary.trade_count for item in oos)
    oos_wins = sum(item.summary.winning_trade_count for item in oos)
    mean_oos = _mean_decimal(tuple(item.summary.total_return_fraction for item in oos))
    mean_dd = _mean_decimal(tuple(item.summary.maximum_drawdown_fraction for item in oos))
    mean_is = _mean_decimal(tuple(item.summary.total_return_fraction for item in is_windows))
    gap = None
    if mean_is is not None and mean_oos is not None:
        gap = _canonical_decimal(Decimal(mean_is) - Decimal(mean_oos))
    win_rate = None
    if oos_trades:
        win_rate = _canonical_decimal(Decimal(oos_wins) / Decimal(oos_trades))
    return StudyAggregate(
        window_count=len(windows),
        oos_window_count=len(oos),
        oos_trade_count=oos_trades,
        oos_winning_trade_count=oos_wins,
        oos_win_rate=win_rate,
        mean_oos_return_fraction=mean_oos,
        mean_oos_drawdown_fraction=mean_dd,
        mean_is_return_fraction=mean_is,
        is_oos_return_gap=gap,
    )


@dataclass(frozen=True, slots=True)
class ResearchStudyService:
    """Plan and submit studies by composing existing backtest submissions."""

    publications: StrategyPublicationStore
    submitter: BacktestSubmitter
    results: BacktestResultReader
    catalog: ResearchStudyCatalog | None = None

    async def plan(self, request: ResearchStudyRequest) -> ResearchStudyPlan:
        """Return the window schedule after loading published strategies."""
        published = await self._load_publications(request)
        published = _merge_derived_candidates(request, published)
        return plan_study(request, publications=published)

    async def submit(self, request: ResearchStudyRequest) -> ResearchStudy:
        """Submit or reuse each child backtest and assemble the derived study."""
        return await self.submit_with_progress(request)

    async def submit_with_progress(
        self,
        request: ResearchStudyRequest,
        *,
        on_progress: Callable[[int, int], Awaitable[object]] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> ResearchStudy:
        """Submit or reuse each child backtest and assemble the derived study."""
        published = await self._publish_derived_candidates(
            request, await self._load_publications(request)
        )
        plan = plan_study(request, publications=published)
        existing = await self._load_existing_plan(plan.plan_fingerprint)
        if existing is not None:
            return existing
        children, loaded_results = await self._submit_windows(
            request,
            plan,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        windows = _annotate_selections(request, children)
        stitched = _derived_stitched_equity(request, windows, loaded_results)
        selected_only = request.kind is StudyKind.WALK_FORWARD_OPTIMIZATION
        assembled = ResearchStudy(
            study_fingerprint="sha256:" + ("0" * 64),
            request_fingerprint=plan.request_fingerprint,
            kind=request.kind,
            engine_contract_version=request.engine_contract_version,
            windows=windows,
            aggregate=aggregate_windows(windows, selected_only=selected_only),
            warnings=_stitch_warnings(plan.warnings, stitched),
            selection_metric=(
                request.selection_metric
                if request.kind in {StudyKind.PARAMETER_SWEEP, StudyKind.WALK_FORWARD_OPTIMIZATION}
                else None
            ),
            stitched_oos_equity=stitched,
        )
        study = assembled.model_copy(update={"study_fingerprint": study_fingerprint(assembled)})
        await self._persist_catalog(study, plan)
        return study

    async def _load_existing_plan(self, plan_fingerprint_value: str) -> ResearchStudy | None:
        """Return a persisted study when the effective plan already exists."""
        if self.catalog is None:
            return None
        try:
            canonical = await self.catalog.find_by_plan_fingerprint(plan_fingerprint_value)
        except StudyCatalogUnavailableError as error:
            raise ResearchStudyError("Research study catalog is unavailable.") from error
        if canonical is None:
            return None
        return ResearchStudy.model_validate_json(canonical)

    async def _persist_catalog(self, study: ResearchStudy, plan: ResearchStudyPlan) -> None:
        """Store the assembled study when a catalog is configured."""
        if self.catalog is None:
            return
        try:
            await self.catalog.persist(
                study_catalog_summary(study, plan),
                canonical_study_json(study),
            )
        except StudyCatalogUnavailableError as error:
            raise ResearchStudyError("Research study catalog is unavailable.") from error

    async def _submit_windows(
        self,
        request: ResearchStudyRequest,
        plan: ResearchStudyPlan,
        *,
        on_progress: Callable[[int, int], Awaitable[object]] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[tuple[StudyWindowResult, ...], dict[str, BacktestResult]]:
        """Submit every planned child and keep result documents for stitching."""
        children: list[StudyWindowResult] = []
        loaded_results: dict[str, BacktestResult] = {}
        total = len(plan.windows)
        try:
            for index, window in enumerate(plan.windows, start=1):
                if cancel_check is not None and await cancel_check():
                    _raise_cancelled_study()
                if on_progress is not None:
                    await on_progress(index - 1, total)
                submission = window_submission_request(request, window)
                identities = await self.submitter.submit(submission)
                result = await self.results.load(identities.result_fingerprint)
                loaded_results[identities.result_fingerprint] = result
                children.append(
                    StudyWindowResult(
                        label=window.label,
                        role=window.role,
                        fold_index=window.fold_index,
                        product_id=window.product_id,
                        run_fingerprint=identities.run_fingerprint,
                        result_fingerprint=identities.result_fingerprint,
                        strategy_fingerprint=window.strategy_fingerprint,
                        evaluation_start=window.evaluation_start,
                        evaluation_end=window.evaluation_end,
                        summary=result.summary,
                    )
                )
            if on_progress is not None:
                await on_progress(total, total)
        except StudyPlanningError:
            raise
        except BacktestSubmissionRejectedError:
            raise
        except ResearchStudyError:
            raise
        except Exception as error:
            raise ResearchStudyError("Research study submission is unavailable.") from error
        return tuple(children), loaded_results

    async def _publish_derived_candidates(
        self,
        request: ResearchStudyRequest,
        publications: dict[str, PublishedStrategy],
    ) -> dict[str, PublishedStrategy]:
        """Persist missing axis-derived fingerprints, then return the merged map."""
        merged = _merge_derived_candidates(request, publications)
        if not request.parameter_axes:
            return merged
        published = dict(publications)
        try:
            for fingerprint, candidate in merged.items():
                if fingerprint in publications:
                    continue
                loaded = await self._load_or_publish_derived(fingerprint, candidate)
                published[loaded.strategy_fingerprint] = loaded
        except StudyPlanningError:
            raise
        except StrategyPublicationError as error:
            if "was not found" in str(error):
                raise StudyPlanningError(str(error)) from error
            raise ResearchStudyError("Research study submission is unavailable.") from error
        except Exception as error:
            raise ResearchStudyError("Research study submission is unavailable.") from error
        return published

    async def _load_or_publish_derived(
        self,
        fingerprint: str,
        candidate: PublishedStrategy,
    ) -> PublishedStrategy:
        """Reuse a stored derived fingerprint or persist it for the first time."""
        try:
            return await self.publications.load(fingerprint)
        except StrategyPublicationError as error:
            if "was not found" not in str(error):
                raise
            return await self.publications.publish(candidate.definition)

    async def _load_publications(
        self, request: ResearchStudyRequest
    ) -> dict[str, PublishedStrategy]:
        """Load every published strategy named by the study request."""
        fingerprints = _strategy_fingerprints(request)
        loaded: dict[str, PublishedStrategy] = {}
        try:
            for fingerprint in fingerprints:
                loaded[fingerprint] = await self.publications.load(fingerprint)
        except StrategyPublicationError as error:
            if "was not found" in str(error):
                raise StudyPlanningError("Published strategy was not found.") from error
            raise ResearchStudyError("Research study submission is unavailable.") from error
        except StudyPlanningError:
            raise
        except Exception as error:
            raise ResearchStudyError("Research study submission is unavailable.") from error
        return loaded


def _strategy_fingerprints(request: ResearchStudyRequest) -> tuple[str, ...]:
    """Collect unique strategy fingerprints for publication loads."""
    if request.kind is StudyKind.CROSS_MARKET:
        if request.markets is None:
            raise StudyPlanningError("cross_market studies require markets.")
        return tuple(dict.fromkeys(item.strategy_fingerprint for item in request.markets))
    if request.strategy_fingerprint is None:
        raise StudyPlanningError("This study kind requires strategy_fingerprint.")
    fingerprints = [request.strategy_fingerprint]
    fingerprints.extend(request.candidate_strategy_fingerprints)
    return tuple(dict.fromkeys(fingerprints))


def _raise_cancelled_study() -> None:
    """Abort study submission when a durable job cancellation was requested."""
    raise ResearchStudyError("Research job was cancelled.")


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
            raise ValueError("candidate_strategy_fingerprints requires between 2 and 8 values")
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


def _plan_cross_market(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
) -> tuple[list[PlannedStudyWindow], DatasetTimeframe]:
    """Emit one full-window child per distinct product."""
    if request.markets is None:
        raise StudyPlanningError("cross_market studies require markets.")
    windows: list[PlannedStudyWindow] = []
    products: list[str] = []
    timeframe: DatasetTimeframe | None = None
    for index, market in enumerate(request.markets):
        published = publications[market.strategy_fingerprint]
        product_id = published.definition.instrument.product_id
        clock = _decision_timeframe(published)
        if timeframe is None:
            timeframe = clock
        elif clock != timeframe:
            raise StudyPlanningError("cross_market markets must share one decision timeframe.")
        if product_id in products:
            raise StudyPlanningError("cross_market markets must use distinct products.")
        products.append(product_id)
        EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
        windows.append(
            PlannedStudyWindow(
                label=product_id,
                role=WindowRole.FULL_WINDOW,
                fold_index=index,
                product_id=product_id,
                timeframe=clock,
                strategy_fingerprint=market.strategy_fingerprint,
                dataset_fingerprint=market.dataset_fingerprint,
                htf_dataset_fingerprint=market.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=market.indicator_dataset_fingerprints,
                evaluation_start=request.evaluation_start,
                evaluation_end=request.evaluation_end,
            )
        )
    if timeframe is None:
        raise StudyPlanningError("cross_market studies require markets.")
    return windows, timeframe


def _plan_single_market(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
    warnings: list[str],
) -> tuple[list[PlannedStudyWindow], DatasetTimeframe]:
    """Emit IS/OOS windows for holdout or walk-forward validation."""
    if request.strategy_fingerprint is None or request.dataset_fingerprint is None:
        raise StudyPlanningError("This study kind requires strategy and dataset fingerprints.")
    published = publications[request.strategy_fingerprint]
    timeframe = _decision_timeframe(published)
    duration = parse_candle_interval(timeframe).duration
    product_id = published.definition.instrument.product_id
    total_bars = _bar_count(request.evaluation_start, request.evaluation_end, duration)
    splits = (
        _oos_splits(request, total_bars, duration)
        if request.kind is StudyKind.OOS_HOLDOUT
        else _walk_forward_splits(request, total_bars, duration, warnings)
    )
    windows: list[PlannedStudyWindow] = []
    for fold_index, role, start, end in splits:
        label = f"{role.value}-{fold_index}"
        windows.append(
            PlannedStudyWindow(
                label=label,
                role=role,
                fold_index=fold_index,
                product_id=product_id,
                timeframe=timeframe,
                strategy_fingerprint=request.strategy_fingerprint,
                dataset_fingerprint=request.dataset_fingerprint,
                htf_dataset_fingerprint=request.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=request.indicator_dataset_fingerprints,
                evaluation_start=start,
                evaluation_end=end,
            )
        )
    return windows, timeframe


def _plan_parameter_sweep(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
    warnings: list[str],
) -> tuple[list[PlannedStudyWindow], DatasetTimeframe]:
    """Emit one full-window child per candidate on the shared evaluation bounds."""
    candidates, timeframe, product_id = _resolve_candidates(request, publications)
    dataset_fingerprint = _required_dataset_fingerprint(request)
    warnings.append(
        "parameter_sweep aggregate is the equal-weight mean of candidate windows, "
        "not an out-of-sample claim."
    )
    windows: list[PlannedStudyWindow] = []
    for index, fingerprint in enumerate(candidates):
        windows.append(
            PlannedStudyWindow(
                label=f"sweep-{index}",
                role=WindowRole.SWEEP_CANDIDATE,
                fold_index=index,
                product_id=product_id,
                timeframe=timeframe,
                strategy_fingerprint=fingerprint,
                dataset_fingerprint=dataset_fingerprint,
                htf_dataset_fingerprint=request.htf_dataset_fingerprint,
                indicator_dataset_fingerprints=request.indicator_dataset_fingerprints,
                evaluation_start=request.evaluation_start,
                evaluation_end=request.evaluation_end,
            )
        )
    return windows, timeframe


def _plan_walk_forward_optimization(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
    warnings: list[str],
) -> tuple[list[PlannedStudyWindow], DatasetTimeframe]:
    """Emit IS and OOS children for every candidate on every fold."""
    candidates, timeframe, product_id = _resolve_candidates(request, publications)
    dataset_fingerprint = _required_dataset_fingerprint(request)
    duration = parse_candle_interval(timeframe).duration
    total_bars = _bar_count(request.evaluation_start, request.evaluation_end, duration)
    splits = _walk_forward_splits(request, total_bars, duration, warnings)
    windows: list[PlannedStudyWindow] = []
    for fold_index, role, start, end in splits:
        for candidate_index, fingerprint in enumerate(candidates):
            windows.append(
                PlannedStudyWindow(
                    label=f"{role.value}-{fold_index}-{candidate_index}",
                    role=role,
                    fold_index=fold_index,
                    product_id=product_id,
                    timeframe=timeframe,
                    strategy_fingerprint=fingerprint,
                    dataset_fingerprint=dataset_fingerprint,
                    htf_dataset_fingerprint=request.htf_dataset_fingerprint,
                    indicator_dataset_fingerprints=request.indicator_dataset_fingerprints,
                    evaluation_start=start,
                    evaluation_end=end,
                )
            )
    return windows, timeframe


def _resolve_candidates(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
) -> tuple[tuple[str, ...], DatasetTimeframe, str]:
    """Return candidate fingerprints that share the base product and timeframe."""
    if request.strategy_fingerprint is None or request.dataset_fingerprint is None:
        raise StudyPlanningError("This study kind requires strategy and dataset fingerprints.")
    base = publications[request.strategy_fingerprint]
    timeframe = _decision_timeframe(base)
    product_id = base.definition.instrument.product_id
    if request.parameter_axes:
        derived = _derived_from_axes(request, base)
        fingerprints = tuple(item.strategy_fingerprint for item in derived)
        return fingerprints, timeframe, product_id
    fingerprints = request.candidate_strategy_fingerprints
    for fingerprint in fingerprints:
        published = publications.get(fingerprint)
        if published is None:
            raise StudyPlanningError(f"Published strategy was not found: {fingerprint}.")
        if _decision_timeframe(published) != timeframe:
            raise StudyPlanningError("Sweep candidates must share one decision timeframe.")
        if published.definition.instrument.product_id != product_id:
            raise StudyPlanningError("Sweep candidates must share one product.")
    return fingerprints, timeframe, product_id


def _merge_derived_candidates(
    request: ResearchStudyRequest,
    publications: dict[str, PublishedStrategy],
) -> dict[str, PublishedStrategy]:
    """Add in-memory axis-derived publications without persisting them."""
    if not request.parameter_axes:
        return publications
    if request.strategy_fingerprint is None:
        raise StudyPlanningError("parameter_axes require strategy_fingerprint.")
    base = publications[request.strategy_fingerprint]
    merged = dict(publications)
    for item in _derived_from_axes(request, base):
        merged[item.strategy_fingerprint] = item
    return merged


def _derived_from_axes(
    request: ResearchStudyRequest,
    base: PublishedStrategy,
) -> tuple[PublishedStrategy, ...]:
    """Derive axis candidates or convert validation failures into planning errors."""
    if request.strategy_fingerprint is None:
        raise StudyPlanningError("parameter_axes require strategy_fingerprint.")
    try:
        return derive_parameter_candidates(
            base.definition,
            request.parameter_axes,
            base_fingerprint=request.strategy_fingerprint,
        )
    except ValueError as error:
        raise StudyPlanningError(str(error)) from error


def _annotate_selections(
    request: ResearchStudyRequest,
    windows: tuple[StudyWindowResult, ...],
) -> tuple[StudyWindowResult, ...]:
    """Mark in-sample winners and their matching OOS or sweep children."""
    if request.kind is StudyKind.PARAMETER_SWEEP:
        scored = tuple(
            (item.strategy_fingerprint, metric_value(item.summary, request.selection_metric))
            for item in windows
        )
        winner = select_candidate_fingerprint(scored, request.selection_metric)
        return tuple(
            item.model_copy(update={"selected": item.strategy_fingerprint == winner})
            for item in windows
        )
    if request.kind is not StudyKind.WALK_FORWARD_OPTIMIZATION:
        return windows
    selected_by_fold = _wfo_selected_by_fold(request, windows)
    return tuple(
        item.model_copy(
            update={"selected": selected_by_fold.get(item.fold_index) == item.strategy_fingerprint}
        )
        for item in windows
    )


def _wfo_selected_by_fold(
    request: ResearchStudyRequest,
    windows: tuple[StudyWindowResult, ...],
) -> dict[int, str]:
    """Choose one fingerprint per fold from in-sample scores only."""
    selected_by_fold: dict[int, str] = {}
    for fold_index in dict.fromkeys(item.fold_index for item in windows):
        insample = tuple(
            item
            for item in windows
            if item.fold_index == fold_index and item.role is WindowRole.IN_SAMPLE
        )
        scored = tuple(
            (item.strategy_fingerprint, metric_value(item.summary, request.selection_metric))
            for item in insample
        )
        selected_by_fold[fold_index] = select_candidate_fingerprint(
            scored, request.selection_metric
        )
    return selected_by_fold


def _derived_stitched_equity(
    request: ResearchStudyRequest,
    windows: tuple[StudyWindowResult, ...],
    results: dict[str, BacktestResult],
) -> StitchedOosEquity | None:
    """Stitch scored OOS paths when the study kind and window geometry allow it."""
    if request.kind not in {
        StudyKind.WALK_FORWARD,
        StudyKind.WALK_FORWARD_OPTIMIZATION,
    }:
        return None
    oos = tuple(item for item in windows if item.role is WindowRole.OUT_OF_SAMPLE)
    if request.kind is StudyKind.WALK_FORWARD_OPTIMIZATION:
        oos = tuple(item for item in oos if item.selected)
    sources: list[StitchSourceWindow] = []
    for item in oos:
        result = results.get(item.result_fingerprint)
        if result is None:
            return unavailable_stitched_equity(
                "Stitched OOS equity is unavailable because a child result could not be loaded."
            )
        sources.append(
            StitchSourceWindow(
                fold_index=item.fold_index,
                evaluation_start=item.evaluation_start,
                evaluation_end=item.evaluation_end,
                result_fingerprint=item.result_fingerprint,
                result=result,
            )
        )
    return stitch_oos_equity(tuple(sources))


def _stitch_warnings(
    warnings: tuple[str, ...],
    stitched: StitchedOosEquity | None,
) -> tuple[str, ...]:
    """Append an explicit stitch-unavailable reason when one exists."""
    extra: list[str] = []
    if (
        stitched is not None
        and not stitched.available
        and stitched.reason is not None
        and stitched.reason not in warnings
    ):
        extra.append(stitched.reason)
    if (
        stitched is not None
        and stitched.available
        and stitched.point_count > 0
        and not stitched.points
    ):
        extra.append(
            "Stitched OOS equity summaries are present; the point series was omitted "
            "because it exceeded 4096 marks."
        )
    return warnings + tuple(extra)


def _oos_splits(
    request: ResearchStudyRequest,
    total_bars: int,
    duration: timedelta,
) -> tuple[tuple[int, WindowRole, datetime, datetime], ...]:
    """Split the evaluation range into one IS window and one OOS window."""
    if request.oos_fraction is None:
        raise StudyPlanningError("oos_fraction is required for oos_holdout.")
    fraction = Decimal(request.oos_fraction)
    oos_bars = int((Decimal(total_bars) * fraction).to_integral_value(rounding=ROUND_HALF_EVEN))
    oos_bars = min(max(oos_bars, 1), total_bars - 1)
    remaining = total_bars - oos_bars - request.embargo_bars
    if remaining < 1:
        raise StudyPlanningError(
            "oos_holdout needs at least one in-sample bar after embargo and OOS."
        )
    is_end = _add_bars(request.evaluation_start, remaining, duration)
    oos_start = _add_bars(is_end, request.embargo_bars, duration)
    return (
        (0, WindowRole.IN_SAMPLE, request.evaluation_start, is_end),
        (0, WindowRole.OUT_OF_SAMPLE, oos_start, request.evaluation_end),
    )


def _walk_forward_splits(
    request: ResearchStudyRequest,
    total_bars: int,
    duration: timedelta,
    warnings: list[str],
) -> tuple[tuple[int, WindowRole, datetime, datetime], ...]:
    """Emit sequential IS/OOS folds until the next OOS would pass evaluation_end."""
    if (
        request.in_sample_bars is None
        or request.out_of_sample_bars is None
        or request.step_bars is None
    ):
        raise StudyPlanningError("walk_forward requires bar counts.")
    if request.step_bars < request.out_of_sample_bars:
        warnings.append(
            "step_bars is smaller than out_of_sample_bars, so OOS windows overlap. "
            "Overlapping OOS metrics are not independent."
        )
    folds: list[tuple[int, WindowRole, datetime, datetime]] = []
    fold_index = 0
    while fold_index < _MAX_FOLDS:
        is_bars = request.in_sample_bars
        if request.fold_mode is FoldMode.ANCHORED:
            is_bars = request.in_sample_bars + fold_index * request.step_bars
        is_offset = 0 if request.fold_mode is FoldMode.ANCHORED else fold_index * request.step_bars
        is_start = _add_bars(request.evaluation_start, is_offset, duration)
        is_end = _add_bars(is_start, is_bars, duration)
        oos_start = _add_bars(is_end, request.embargo_bars, duration)
        oos_end = _add_bars(oos_start, request.out_of_sample_bars, duration)
        if oos_end > request.evaluation_end:
            break
        needed = is_offset + is_bars + request.embargo_bars + request.out_of_sample_bars
        if needed > total_bars:
            break
        folds.append((fold_index, WindowRole.IN_SAMPLE, is_start, is_end))
        folds.append((fold_index, WindowRole.OUT_OF_SAMPLE, oos_start, oos_end))
        fold_index += 1
    if not folds:
        raise StudyPlanningError(
            "The evaluation window is too short for one walk-forward fold "
            "(in-sample, embargo, and out-of-sample bars)."
        )
    return tuple(folds)


def _required_dataset_fingerprint(request: ResearchStudyRequest) -> str:
    """Return the single-market dataset fingerprint or fail closed."""
    if request.dataset_fingerprint is None:
        raise StudyPlanningError("This study kind requires dataset_fingerprint.")
    return request.dataset_fingerprint


def _decision_timeframe(published: PublishedStrategy) -> DatasetTimeframe:
    """Read the published LTF clock without inventing unsupported intervals."""
    return published.definition.timeframe


def _bar_count(start: datetime, end: datetime, duration: timedelta) -> int:
    """Return the integer number of bars in a half-open window."""
    seconds = int((end - start).total_seconds())
    step = int(duration.total_seconds())
    if step <= 0 or seconds <= 0 or seconds % step != 0:
        raise StudyPlanningError("evaluation window must be a positive integer number of bars.")
    return seconds // step


def _add_bars(instant: datetime, bars: int, duration: timedelta) -> datetime:
    """Advance a UTC boundary by an exact bar count."""
    return instant + duration * bars


def _mean_decimal(values: tuple[str, ...]) -> str | None:
    """Equal-weight mean of canonical decimal strings, or None when empty."""
    if not values:
        return None
    total = sum((Decimal(item) for item in values), start=Decimal(0))
    return _canonical_decimal(total / Decimal(len(values)))


def _canonical_decimal(value: Decimal) -> str:
    """Render a finite Decimal as a plain string without scientific notation."""
    quantized = value.quantize(Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
