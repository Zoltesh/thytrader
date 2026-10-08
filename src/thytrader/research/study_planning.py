"""Plan research study child windows within the candidate and window budgets."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.submission import BacktestSubmissionRequest
from thytrader.evaluation.models import EvaluationWindow
from thytrader.market_data.models import DatasetTimeframe, parse_candle_interval
from thytrader.research.parameter_sweep import derive_parameter_candidates
from thytrader.research.study_identity import (
    _FINGERPRINT_PREFIX,
    plan_fingerprint,
    request_fingerprint,
)
from thytrader.research.study_models import (
    ASYNC_STUDY_BUDGET,
    SYNC_STUDY_BUDGET,
    FoldMode,
    PlannedStudyWindow,
    ResearchStudyPlan,
    ResearchStudyRequest,
    StudyBudget,
    StudyKind,
    WindowRole,
)
from thytrader.research.sweep_axes import MAX_CANDIDATES, parameter_axes_candidate_count

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from thytrader.strategies.snapshots import StrategySnapshot


_MAX_FOLDS = 24


class StudyPlanningError(ValueError):
    """Reject a study request that cannot form a valid window schedule."""


class StudyBudgetError(StudyPlanningError):
    """Reject a study whose candidates or child windows exceed its execution budget."""


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
