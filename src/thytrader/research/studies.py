"""Walk-forward, OOS, cross-market, sweep, and WFO research study composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from hashlib import sha256
import json
import logging
from typing import TYPE_CHECKING

from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
)
from thytrader.evaluation.models import EvaluationWindow
from thytrader.evaluation.publication import explain_evaluation_window_rejection
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.market_data.models import DatasetTimeframe, parse_candle_interval
from thytrader.research.catalog import (
    ResearchStudyCatalog,
    StudyCatalogSummary,
    StudyCatalogUnavailableError,
)
from thytrader.research.parameter_sweep import (
    MAX_CANDIDATES,
    AxisValue,
    StitchedOosEquity,
    StitchSourceWindow,
    candidate_axis_values,
    derive_parameter_candidates,
    downsample_stitched_points,
    metric_value,
    parameter_axes_candidate_count,
    select_candidate_fingerprint,
    stitch_oos_equity,
    unavailable_stitched_equity,
)
from thytrader.research.study_models import (
    ASYNC_STUDY_BUDGET,
    STUDY_CONTRACT_VERSION,
    SYNC_STUDY_BUDGET,
    FoldMode,
    MarketBinding,
    PlannedStudyWindow,
    ResearchStudy,
    ResearchStudyPlan,
    ResearchStudyPlanSummary,
    ResearchStudyRequest,
    ResearchStudySummary,
    StudyAggregate,
    StudyBudget,
    StudyCandidateAggregate,
    StudyFailedPhase,
    StudyKind,
    StudyWindowPnl,
    StudyWindowResult,
    WindowRole,
)
from thytrader.strategies.snapshots import StrategySnapshotError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from thytrader.backtest.models import BacktestResult
    from thytrader.backtest.submission import BacktestSubmitter
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.persistence.backtest_results import BacktestResultReader
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import (
        StrategySnapshot,
        StrategySnapshotReader,
        StrategySnapshotStore,
    )

__all__ = [
    "ASYNC_STUDY_BUDGET",
    "STUDY_CONTRACT_VERSION",
    "SYNC_STUDY_BUDGET",
    "FoldMode",
    "MarketBinding",
    "PlannedStudyWindow",
    "ResearchStudy",
    "ResearchStudyError",
    "ResearchStudyPlan",
    "ResearchStudyPlanSummary",
    "ResearchStudyRequest",
    "ResearchStudyService",
    "ResearchStudySummary",
    "StudyAggregate",
    "StudyBudget",
    "StudyBudgetError",
    "StudyCandidateAggregate",
    "StudyFailedPhase",
    "StudyKind",
    "StudyPlanningError",
    "StudyWindowPnl",
    "StudyWindowResult",
    "WindowRole",
    "aggregate_windows",
    "canonical_study_json",
    "load_candidate_definitions",
    "plan_fingerprint",
    "plan_study",
    "request_fingerprint",
    "study_candidate_aggregates",
    "study_candidate_count",
    "study_candidate_fingerprints",
    "study_catalog_summary",
    "study_fingerprint",
    "summarize_research_study",
    "summarize_research_study_plan",
    "window_submission_request",
]

_logger = logging.getLogger(__name__)
_FINGERPRINT_PREFIX = "sha256:"
_MAX_FOLDS = 24


class StudyPlanningError(ValueError):
    """Reject a study request that cannot form a valid window schedule."""


class StudyBudgetError(StudyPlanningError):
    """Reject a study whose candidates or child windows exceed its execution budget."""


class ResearchStudyError(RuntimeError):
    """Report a redacted study failure without trading authority.

    ``failed_phase`` names the submission stage that failed so operators and
    agents can decide what to inspect or retry. It stays ``None`` on plain
    construction for backward compatibility.
    """

    def __init__(self, message: str, *, failed_phase: str | None = None) -> None:
        """Store the message and the optional structured failure phase."""
        super().__init__(message)
        self.failed_phase = failed_phase


def summarize_research_study(
    study: ResearchStudy,
    *,
    definitions: Mapping[str, StrategyDefinition] | None = None,
) -> ResearchStudySummary:
    """Project one persisted study into a bounded summary document.

    Args:
        study: The canonical study.
        definitions: Candidate definitions by strategy fingerprint. When given, every
            row and candidate names its sweep ``axis_values`` (recovered from the
            definitions, ADR 0094); a missing definition leaves that candidate's ``{}``.

    The stitched OOS path is thinned for display (``point_count`` stays the full count
    and ``stitched_oos_points_downsampled`` says whether marks were dropped); the full
    study (``detail=full``) keeps every mark.
    """
    stitched = study.stitched_oos_equity
    downsampled = False
    if stitched is not None and stitched.points:
        points = downsample_stitched_points(stitched.points)
        downsampled = len(points) != len(stitched.points)
        stitched = stitched.model_copy(update={"points": points})
    axes = _study_axis_values(study, definitions or {})
    return ResearchStudySummary(
        study_fingerprint=study.study_fingerprint,
        request_fingerprint=study.request_fingerprint,
        kind=study.kind,
        aggregate=study.aggregate,
        warnings=study.warnings,
        selection_metric=study.selection_metric,
        stitched_oos_equity=stitched,
        stitched_oos_points_downsampled=downsampled,
        window_count=len(study.windows),
        window_pnl=tuple(
            StudyWindowPnl(
                label=window.label,
                role=window.role,
                fold_index=window.fold_index,
                product_id=window.product_id,
                evaluation_start=window.evaluation_start,
                evaluation_end=window.evaluation_end,
                result_fingerprint=window.result_fingerprint,
                strategy_fingerprint=window.strategy_fingerprint,
                axis_values=axes.get(window.strategy_fingerprint, {}),
                total_net_pnl=window.summary.total_net_pnl,
                total_return_fraction=window.summary.total_return_fraction,
                trade_count=window.summary.trade_count,
                selected=window.selected,
            )
            for window in study.windows
        ),
        candidates=study_candidate_aggregates(study, axes),
    )


async def load_candidate_definitions(
    publications: StrategySnapshotReader, study: ResearchStudy
) -> dict[str, StrategyDefinition]:
    """Load every candidate snapshot a study names, skipping ones that cannot be read.

    Axis values are explanatory, so an unreadable snapshot leaves that candidate's
    ``axis_values`` empty instead of failing the summary.
    """
    definitions: dict[str, StrategyDefinition] = {}
    for fingerprint in study_candidate_fingerprints(study):
        try:
            definitions[fingerprint] = (await publications.load(fingerprint)).definition
        except Exception as error:  # noqa: BLE001 - advisory enrichment only.
            _logger.warning(
                "study_candidate_snapshot_unavailable error_class=%s", type(error).__name__
            )
    return definitions


def study_candidate_fingerprints(study: ResearchStudy) -> tuple[str, ...]:
    """Distinct child strategy fingerprints in first-appearance order."""
    return tuple(dict.fromkeys(window.strategy_fingerprint for window in study.windows))


def _study_axis_values(
    study: ResearchStudy, definitions: Mapping[str, StrategyDefinition]
) -> dict[str, dict[str, AxisValue]]:
    """Axis assignments for the study's candidates whose definitions are known."""
    known = {
        fingerprint: definitions[fingerprint]
        for fingerprint in study_candidate_fingerprints(study)
        if fingerprint in definitions
    }
    return candidate_axis_values(known)


def study_candidate_aggregates(
    study: ResearchStudy, axes: Mapping[str, dict[str, AxisValue]]
) -> tuple[StudyCandidateAggregate, ...]:
    """Sum every window per candidate, in first-appearance order."""
    aggregates: list[StudyCandidateAggregate] = []
    for fingerprint in study_candidate_fingerprints(study):
        windows = tuple(item for item in study.windows if item.strategy_fingerprint == fingerprint)
        insample = tuple(item for item in windows if item.role is WindowRole.IN_SAMPLE)
        oos = tuple(item for item in windows if item.role is WindowRole.OUT_OF_SAMPLE)
        full = tuple(
            item
            for item in windows
            if item.role in {WindowRole.FULL_WINDOW, WindowRole.SWEEP_CANDIDATE}
        )
        aggregates.append(
            StudyCandidateAggregate(
                strategy_fingerprint=fingerprint,
                product_id=windows[0].product_id,
                axis_values=axes.get(fingerprint, {}),
                window_count=len(windows),
                selected_window_count=sum(1 for item in windows if item.selected),
                in_sample_window_count=len(insample),
                in_sample_total_net_pnl=_pnl_sum(insample),
                oos_window_count=len(oos),
                oos_total_net_pnl=_pnl_sum(oos),
                oos_positive_window_count=sum(
                    1 for item in oos if Decimal(item.summary.total_net_pnl) > 0
                ),
                oos_trade_count=sum(item.summary.trade_count for item in oos),
                full_window_count=len(full),
                full_window_total_net_pnl=_pnl_sum(full),
            )
        )
    return tuple(aggregates)


def _pnl_sum(windows: tuple[StudyWindowResult, ...]) -> str | None:
    """Exact sum of the windows' net PnL, or None when there are none."""
    if not windows:
        return None
    total = sum((Decimal(item.summary.total_net_pnl) for item in windows), start=Decimal(0))
    return _canonical_decimal(total)


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
    publications: dict[str, StrategySnapshot],
    budget: StudyBudget = ASYNC_STUDY_BUDGET,
) -> ResearchStudyPlan:
    """Build the child window schedule from published strategy metadata.

    ``budget`` bounds candidates and child windows for the way the study will run.
    Warnings depend only on the request, so plan and submit agree on them.
    """
    warnings: list[str] = []
    _require_candidate_budget(request, budget)
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
    _require_window_budget(len(windows), budget)
    warnings.extend(_sync_budget_warnings(request, len(windows)))
    plan = ResearchStudyPlan(
        kind=request.kind,
        request_fingerprint=request_fingerprint(request),
        plan_fingerprint=_FINGERPRINT_PREFIX + ("0" * 64),
        timeframe=timeframe,
        windows=tuple(windows),
        warnings=tuple(warnings),
    )
    return plan.model_copy(update={"plan_fingerprint": plan_fingerprint(plan)})


def study_candidate_count(request: ResearchStudyRequest) -> int:
    """Return how many candidate strategies a sweep or WFO searches (0 for other kinds)."""
    if request.parameter_axes:
        return parameter_axes_candidate_count(request.parameter_axes)
    return len(request.candidate_strategy_fingerprints)


def _require_candidate_budget(request: ResearchStudyRequest, budget: StudyBudget) -> None:
    """Refuse a grid larger than this execution mode allows, naming the async escape."""
    count = study_candidate_count(request)
    if count <= budget.max_candidates:
        return
    if budget.mode == "sync":
        raise StudyBudgetError(
            f"This study searches {count} candidates; a synchronous submit allows at most "
            f"{budget.max_candidates}. Submit it as an async job (submit-study --async, "
            f"POST /api/v1/research/studies?async=true), which allows up to {MAX_CANDIDATES}."
        )
    raise StudyBudgetError(
        f"This study searches {count} candidates; at most {budget.max_candidates} are allowed."
    )


def _require_window_budget(window_count: int, budget: StudyBudget) -> None:
    """Refuse a schedule with more child backtests than this execution mode allows."""
    if window_count <= budget.max_windows:
        return
    if budget.mode == "sync":
        raise StudyBudgetError(
            f"This study plans {window_count} child windows; a synchronous submit allows at "
            f"most {budget.max_windows}. Submit it as an async job (submit-study --async), "
            f"which allows up to {ASYNC_STUDY_BUDGET.max_windows}, or reduce folds or candidates."
        )
    raise StudyBudgetError(
        f"This study plans {window_count} child windows; an async job allows at most "
        f"{budget.max_windows}. Reduce folds (larger step_bars) or candidates."
    )


def _sync_budget_warnings(request: ResearchStudyRequest, window_count: int) -> tuple[str, ...]:
    """Explain an async-only study and the overfitting cost of a large search."""
    warnings: list[str] = []
    count = study_candidate_count(request)
    if count > SYNC_STUDY_BUDGET.max_candidates or window_count > SYNC_STUDY_BUDGET.max_windows:
        warnings.append(
            f"This study ({count} candidates, {window_count} child windows) exceeds the "
            "synchronous budget; it runs only as an async job (submit-study --async)."
        )
    if count > SYNC_STUDY_BUDGET.max_candidates:
        warnings.append(
            f"Searching {count} candidates raises the chance that the best in-sample result is "
            "luck (data snooping). Judge selected out-of-sample windows, never the best "
            "candidate's in-sample or sweep mean."
        )
    return tuple(warnings)


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
        reference_dataset_fingerprints=window.reference_dataset_fingerprints,
        evaluation_start=window.evaluation_start,
        evaluation_end=window.evaluation_end,
        initial_quote_balance=request.initial_quote_balance,
        maker_fee_rate=request.maker_fee_rate,
        taker_fee_rate=request.taker_fee_rate,
        fixed_slippage_bps=request.fixed_slippage_bps,
        spread_bps=request.spread_bps,
        execution_stress=request.execution_stress,
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
    candidates = tuple(item for item in pool if item.role is WindowRole.SWEEP_CANDIDATE)
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
    aggregate = StudyAggregate(
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
    if candidates and not is_windows:
        aggregate = aggregate.model_copy(
            update={
                "oos_window_count": 0,
                "oos_trade_count": 0,
                "oos_winning_trade_count": 0,
                "oos_win_rate": None,
                "mean_oos_return_fraction": None,
                "mean_oos_drawdown_fraction": None,
                "mean_is_return_fraction": None,
                "is_oos_return_gap": None,
                "candidate_window_count": len(candidates),
                "candidate_trade_count": sum(item.summary.trade_count for item in candidates),
                "candidate_winning_trade_count": sum(
                    item.summary.winning_trade_count for item in candidates
                ),
                "mean_candidate_return_fraction": _mean_decimal(
                    tuple(item.summary.total_return_fraction for item in candidates)
                ),
                "mean_candidate_drawdown_fraction": _mean_decimal(
                    tuple(item.summary.maximum_drawdown_fraction for item in candidates)
                ),
            }
        )
    return aggregate


@dataclass(frozen=True, slots=True)
class ResearchStudyService:
    """Plan and submit studies by composing existing backtest submissions."""

    publications: StrategySnapshotStore
    submitter: BacktestSubmitter
    results: BacktestResultReader
    catalog: ResearchStudyCatalog | None = None
    datasets: DatasetStore | None = None

    async def plan(
        self, request: ResearchStudyRequest, *, budget: StudyBudget = ASYNC_STUDY_BUDGET
    ) -> ResearchStudyPlan:
        """Return the window schedule after loading published strategies."""
        published = await self._load_publications(request)
        published = _merge_derived_candidates(request, published)
        plan = plan_study(request, publications=published, budget=budget)
        self._reject_windows_outside_datasets(plan, published)
        return plan

    async def submit(
        self, request: ResearchStudyRequest, *, budget: StudyBudget = SYNC_STUDY_BUDGET
    ) -> ResearchStudy:
        """Submit or reuse each child backtest inside this call (small synchronous budget)."""
        return await self.submit_with_progress(request, budget=budget)

    async def submit_with_progress(
        self,
        request: ResearchStudyRequest,
        *,
        on_progress: Callable[[int, int], Awaitable[object]] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
        budget: StudyBudget = ASYNC_STUDY_BUDGET,
    ) -> ResearchStudy:
        """Submit or reuse each child backtest and assemble the derived study.

        The research worker runs async jobs here with the larger async budget.
        """
        published = await self._publish_derived_candidates(
            request, await self._load_publications(request)
        )
        plan = plan_study(request, publications=published, budget=budget)
        self._reject_windows_outside_datasets(plan, published)
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
            raise ResearchStudyError(
                "Research study catalog is unavailable.",
                failed_phase=StudyFailedPhase.PERSIST_STUDY.value,
            ) from error
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
            raise ResearchStudyError(
                "Research study catalog is unavailable.",
                failed_phase=StudyFailedPhase.PERSIST_STUDY.value,
            ) from error

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
            raise ResearchStudyError(
                f"Child window submission failed: {error}",
                failed_phase=StudyFailedPhase.SUBMIT_CHILDREN.value,
            ) from error
        return tuple(children), loaded_results

    async def _publish_derived_candidates(
        self,
        request: ResearchStudyRequest,
        publications: dict[str, StrategySnapshot],
    ) -> dict[str, StrategySnapshot]:
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
        except StrategySnapshotError as error:
            if "was not found" in str(error):
                raise StudyPlanningError(str(error)) from error
            raise ResearchStudyError(
                f"Derived candidate publication failed: {error}",
                failed_phase=StudyFailedPhase.PUBLISH_DERIVED.value,
            ) from error
        except Exception as error:
            raise ResearchStudyError(
                f"Derived candidate publication failed: {error}",
                failed_phase=StudyFailedPhase.PUBLISH_DERIVED.value,
            ) from error
        return published

    async def _load_or_publish_derived(
        self,
        fingerprint: str,
        candidate: StrategySnapshot,
    ) -> StrategySnapshot:
        """Reuse a stored derived fingerprint or persist it for the first time."""
        try:
            return await self.publications.load(fingerprint)
        except StrategySnapshotError as error:
            if "was not found" not in str(error):
                raise
            return await self.publications.record_snapshot(candidate.definition)

    def _reject_windows_outside_datasets(
        self,
        plan: ResearchStudyPlan,
        publications: dict[str, StrategySnapshot],
    ) -> None:
        """Reject planned windows with the same dataset bound check as child backtests."""
        if self.datasets is None:
            return
        seen: dict[str, tuple[datetime, datetime]] = {}
        for window in plan.windows:
            bounds = seen.get(window.dataset_fingerprint)
            if bounds is None:
                try:
                    manifest = self.datasets.load_manifest(window.dataset_fingerprint)
                except DatasetStoreError as error:
                    raise StudyPlanningError(
                        "The selected dataset was not found or is not a verified complete artifact."
                    ) from error
                try:
                    bounds = (
                        _coverage_instant(manifest.starts_at),
                        _coverage_instant(manifest.ends_at),
                    )
                except ValueError as error:
                    raise StudyPlanningError("Dataset coverage timestamps are invalid.") from error
                seen[window.dataset_fingerprint] = bounds
            published = publications[window.strategy_fingerprint]
            rejection = explain_evaluation_window_rejection(
                dataset_starts_at=bounds[0],
                dataset_ends_at=bounds[1],
                evaluation_start=window.evaluation_start,
                evaluation_end=window.evaluation_end,
                warmup_bars=published.definition.data_requirements.warmup_bars,
                timeframe=published.definition.timeframe,
            )
            if rejection is not None:
                raise StudyPlanningError(rejection)

    async def _load_publications(
        self, request: ResearchStudyRequest
    ) -> dict[str, StrategySnapshot]:
        """Load every published strategy named by the study request."""
        fingerprints = _strategy_fingerprints(request)
        loaded: dict[str, StrategySnapshot] = {}
        try:
            for fingerprint in fingerprints:
                loaded[fingerprint] = await self.publications.load(fingerprint)
        except StrategySnapshotError as error:
            if "was not found" in str(error):
                raise StudyPlanningError("Published strategy was not found.") from error
            raise ResearchStudyError(
                f"Published strategy load failed: {error}",
                failed_phase=StudyFailedPhase.PUBLISH_DERIVED.value,
            ) from error
        except StudyPlanningError:
            raise
        except Exception as error:
            raise ResearchStudyError(
                f"Published strategy load failed: {error}",
                failed_phase=StudyFailedPhase.UNKNOWN.value,
            ) from error
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


def _coverage_instant(value: str) -> datetime:
    """Parse one dataset coverage timestamp as timezone-aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Dataset coverage timestamps are invalid.")
    return parsed.astimezone(UTC)


def _raise_cancelled_study() -> None:
    """Abort study submission when a durable job cancellation was requested."""
    raise ResearchStudyError("Research job was cancelled.")


def _plan_cross_market(
    request: ResearchStudyRequest,
    publications: dict[str, StrategySnapshot],
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
                reference_dataset_fingerprints=market.reference_dataset_fingerprints,
                evaluation_start=request.evaluation_start,
                evaluation_end=request.evaluation_end,
            )
        )
    if timeframe is None:
        raise StudyPlanningError("cross_market studies require markets.")
    return windows, timeframe


def _plan_single_market(
    request: ResearchStudyRequest,
    publications: dict[str, StrategySnapshot],
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
                reference_dataset_fingerprints=request.reference_dataset_fingerprints,
                evaluation_start=start,
                evaluation_end=end,
            )
        )
    return windows, timeframe


def _plan_parameter_sweep(
    request: ResearchStudyRequest,
    publications: dict[str, StrategySnapshot],
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
                reference_dataset_fingerprints=request.reference_dataset_fingerprints,
                evaluation_start=request.evaluation_start,
                evaluation_end=request.evaluation_end,
            )
        )
    return windows, timeframe


def _plan_walk_forward_optimization(
    request: ResearchStudyRequest,
    publications: dict[str, StrategySnapshot],
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
                    reference_dataset_fingerprints=request.reference_dataset_fingerprints,
                    evaluation_start=start,
                    evaluation_end=end,
                )
            )
    return windows, timeframe


def _resolve_candidates(
    request: ResearchStudyRequest,
    publications: dict[str, StrategySnapshot],
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
    publications: dict[str, StrategySnapshot],
) -> dict[str, StrategySnapshot]:
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
    base: StrategySnapshot,
) -> tuple[StrategySnapshot, ...]:
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


def _decision_timeframe(published: StrategySnapshot) -> DatasetTimeframe:
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
