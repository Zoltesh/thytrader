"""Select, stitch, and aggregate scored child windows into a research study."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from thytrader.research.stitched_equity import (
    StitchedOosEquity,
    StitchSourceWindow,
    stitch_oos_equity,
    unavailable_stitched_equity,
)
from thytrader.research.study_models import (
    ResearchStudyRequest,
    StudyAggregate,
    StudyKind,
    StudyWindowResult,
    WindowRole,
)
from thytrader.research.sweep_selection import metric_value, select_candidate_fingerprint

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult


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
