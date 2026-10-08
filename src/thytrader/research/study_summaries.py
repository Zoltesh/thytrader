"""Project research studies and plans into bounded summaries and catalog rows."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.research.catalog import StudyCatalogSummary
from thytrader.research.stitched_equity import downsample_stitched_points
from thytrader.research.study_assembly import _canonical_decimal
from thytrader.research.study_models import (
    ResearchStudy,
    ResearchStudyPlan,
    ResearchStudyPlanSummary,
    ResearchStudySummary,
    StudyCandidateAggregate,
    StudyWindowPnl,
    StudyWindowResult,
    WindowRole,
)
from thytrader.research.sweep_coordinates import candidate_axis_values

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.research.sweep_axes import AxisValue
    from thytrader.strategies.models import StrategyDefinition


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
