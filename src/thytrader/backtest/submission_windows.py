"""Evaluation-window resolution of a backtest submission.

Omitted dates fill to the common last-completed coverage of every bound dataset;
supplied dates are verified against the primary dataset (with a suggestion) and then
against every other bound dataset's coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.backtest.submission_bindings import _reference_bindings_match
from thytrader.backtest.submission_coverage import (
    _load_bound_manifest,
    _manifest_instant,
    _reference_missing_message,
    _require_coverage_windows,
)
from thytrader.backtest.submission_models import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
)
from thytrader.evaluation.models import AdditionalInstrumentDataset, EvaluationWindow
from thytrader.evaluation.multi_timeframe import (
    earliest_evaluation_start_for_closed_bar,
    latest_evaluation_end_for_closed_bar,
)
from thytrader.evaluation.publication import (
    ResearchRunPublicationError,
    dataset_evaluation_bounds,
    explain_evaluation_window_rejection,
)
from thytrader.market_data.datasets import DatasetManifest, DatasetStoreError
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    reference_data_requirements,
    strategy_indicator_operands,
)

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.strategies.snapshots import StrategySnapshot


def _with_evaluation_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> BacktestSubmissionRequest:
    """Fill omitted dates from common coverage, or reject supplied dates with a suggestion."""
    try:
        manifest = dataset_store.load_manifest(request.dataset_fingerprint)
        dataset_starts_at = _manifest_instant(manifest.starts_at)
        dataset_ends_at = _manifest_instant(manifest.ends_at)
        suggested_start, suggested_end = dataset_evaluation_bounds(
            dataset_starts_at=dataset_starts_at,
            dataset_ends_at=dataset_ends_at,
            warmup_bars=strategy.definition.data_requirements.warmup_bars,
            timeframe=strategy.definition.timeframe,
        )
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected dataset was not found or is not a verified complete artifact."
        ) from error
    except (ResearchRunPublicationError, ValueError) as error:
        raise BacktestSubmissionRejectedError(str(error)) from error
    if request.evaluation_start is None and request.evaluation_end is None:
        return _fill_omitted_evaluation_window(
            request,
            strategy,
            dataset_store,
            suggested_start=suggested_start,
            suggested_end=suggested_end,
        )
    if request.evaluation_start is None or request.evaluation_end is None:
        raise BacktestSubmissionRejectedError(
            "evaluation_start and evaluation_end must both be omitted or both be set."
        )
    try:
        EvaluationWindow(starts_at=request.evaluation_start, ends_at=request.evaluation_end)
    except ValueError as error:
        raise BacktestSubmissionRejectedError(str(error)) from error
    rejection = explain_evaluation_window_rejection(
        dataset_starts_at=dataset_starts_at,
        dataset_ends_at=dataset_ends_at,
        evaluation_start=request.evaluation_start,
        evaluation_end=request.evaluation_end,
        warmup_bars=strategy.definition.data_requirements.warmup_bars,
        timeframe=strategy.definition.timeframe,
    )
    if rejection is not None:
        raise BacktestSubmissionRejectedError(rejection)
    _require_coverage_windows(request, strategy, dataset_store)
    return request


def _fill_omitted_evaluation_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
    *,
    suggested_start: datetime,
    suggested_end: datetime,
) -> BacktestSubmissionRequest:
    """Default omitted bounds to the common LTF, HTF, extra-TF, and extra-product coverage."""
    start, end = _intersect_omitted_evaluation_window(
        request,
        strategy,
        dataset_store,
        suggested_start=suggested_start,
        suggested_end=suggested_end,
    )
    if start >= end:
        raise BacktestSubmissionRejectedError(
            "The selected datasets have no common evaluation window after warmup "
            "and last-completed extra-clock coverage."
        )
    filled = request.model_copy(update={"evaluation_start": start, "evaluation_end": end})
    _require_coverage_windows(filled, strategy, dataset_store)
    return filled


def _intersect_omitted_evaluation_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
    *,
    suggested_start: datetime,
    suggested_end: datetime,
) -> tuple[datetime, datetime]:
    """Shrink the LTF usable window to last-completed coverage of every bound extra clock."""
    start, end = suggested_start, suggested_end
    start, end = _intersect_htf_omitted_window(start, end, request, strategy, dataset_store)
    start, end = _intersect_indicator_omitted_window(start, end, request, strategy, dataset_store)
    start, end = _intersect_reference_omitted_window(start, end, request, strategy, dataset_store)
    return _intersect_additional_omitted_window(start, end, request, strategy, dataset_store)


def _intersect_reference_omitted_window(
    start: datetime,
    end: datetime,
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Clip omitted bounds to each reference instrument's last-completed coverage.

    A binding list that does not match the strategy is left for the request check to
    reject with its own message.
    """
    requirements = reference_data_requirements(strategy.definition)
    if not _reference_bindings_match(request, strategy.definition):
        return start, end
    clipped_start, clipped_end = start, end
    for binding, requirement in zip(
        request.reference_dataset_fingerprints, requirements, strict=True
    ):
        manifest = _load_bound_manifest(
            dataset_store,
            binding.dataset_fingerprint,
            missing_message=_reference_missing_message(binding),
        )
        clip_start, clip_end = _closed_bar_clip_from_manifest(
            manifest,
            clock_timeframe=requirement.timeframe,
            warmup_bars=requirement.warmup_bars,
            decision_timeframe=strategy.definition.timeframe,
        )
        clipped_start, clipped_end = _clip_evaluation_window(
            clipped_start, clipped_end, clip_start, clip_end
        )
    return clipped_start, clipped_end


def _clip_evaluation_window(
    start: datetime,
    end: datetime,
    clip_start: datetime,
    clip_end: datetime,
) -> tuple[datetime, datetime]:
    """Return the overlapping half-open evaluation window."""
    return max(start, clip_start), min(end, clip_end)


def _closed_bar_clip_from_manifest(
    manifest: DatasetManifest,
    *,
    clock_timeframe: str,
    warmup_bars: int,
    decision_timeframe: str,
) -> tuple[datetime, datetime]:
    """Translate one extra-clock manifest into a decision-clock evaluation clip."""
    return (
        earliest_evaluation_start_for_closed_bar(
            dataset_starts_at=_manifest_instant(manifest.starts_at),
            timeframe=clock_timeframe,
            warmup_bars=warmup_bars,
            decision_timeframe=decision_timeframe,
        ),
        latest_evaluation_end_for_closed_bar(
            dataset_ends_at=_manifest_instant(manifest.ends_at),
            timeframe=clock_timeframe,
            decision_timeframe=decision_timeframe,
        ),
    )


def _intersect_htf_omitted_window(
    start: datetime,
    end: datetime,
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Clip omitted bounds to last-completed HTF coverage when a filter is declared."""
    htf_filter = strategy.definition.htf_filter
    if htf_filter is None or request.htf_dataset_fingerprint is None:
        return start, end
    manifest = _load_bound_manifest(
        dataset_store,
        request.htf_dataset_fingerprint,
        missing_message=(
            "The selected HTF dataset was not found or is not a verified complete artifact."
        ),
    )
    clip_start, clip_end = _closed_bar_clip_from_manifest(
        manifest,
        clock_timeframe=htf_filter.timeframe,
        warmup_bars=htf_filter.data_requirements.warmup_bars,
        decision_timeframe=strategy.definition.timeframe,
    )
    return _clip_evaluation_window(start, end, clip_start, clip_end)


def _intersect_indicator_omitted_window(
    start: datetime,
    end: datetime,
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Clip omitted bounds to last-completed extra-TF coverage."""
    groups = dict(extra_indicator_timeframe_groups(strategy.definition))
    clipped_start, clipped_end = start, end
    for binding in request.indicator_dataset_fingerprints:
        manifest = _load_bound_manifest(
            dataset_store,
            binding.dataset_fingerprint,
            missing_message=(
                "The selected indicator-timeframe dataset was not found or is not a "
                "verified complete artifact."
            ),
        )
        clip_start, clip_end = _closed_bar_clip_from_manifest(
            manifest,
            clock_timeframe=binding.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                groups[binding.timeframe], operands=strategy_indicator_operands(strategy.definition)
            ),
            decision_timeframe=strategy.definition.timeframe,
        )
        clipped_start, clipped_end = _clip_evaluation_window(
            clipped_start, clipped_end, clip_start, clip_end
        )
    return clipped_start, clipped_end


def _intersect_additional_omitted_window(
    start: datetime,
    end: datetime,
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Clip omitted bounds to extra-product LTF, HTF, and extra-TF coverage."""
    if not request.additional_instrument_datasets:
        return start, end
    clipped_start, clipped_end = start, end
    for binding in request.additional_instrument_datasets:
        clipped_start, clipped_end = _clip_additional_binding_window(
            clipped_start,
            clipped_end,
            binding,
            strategy,
            dataset_store,
        )
    return clipped_start, clipped_end


def _clip_additional_binding_window(
    start: datetime,
    end: datetime,
    binding: AdditionalInstrumentDataset,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Clip omitted bounds to one extra product's bound datasets."""
    definition = strategy.definition
    manifest = _load_bound_manifest(
        dataset_store,
        binding.dataset_fingerprint,
        missing_message=(
            "The selected additional-instrument dataset was not found or is not a "
            "verified complete artifact."
        ),
    )
    extra_start, extra_end = dataset_evaluation_bounds(
        dataset_starts_at=_manifest_instant(manifest.starts_at),
        dataset_ends_at=_manifest_instant(manifest.ends_at),
        warmup_bars=definition.data_requirements.warmup_bars,
        timeframe=definition.timeframe,
    )
    clipped_start, clipped_end = _clip_evaluation_window(start, end, extra_start, extra_end)
    htf_filter = definition.htf_filter
    if htf_filter is not None and binding.htf_dataset_fingerprint is not None:
        htf_manifest = _load_bound_manifest(
            dataset_store,
            binding.htf_dataset_fingerprint,
            missing_message=(
                "The selected additional-instrument HTF dataset was not found or is "
                "not a verified complete artifact."
            ),
        )
        clip_start, clip_end = _closed_bar_clip_from_manifest(
            htf_manifest,
            clock_timeframe=htf_filter.timeframe,
            warmup_bars=htf_filter.data_requirements.warmup_bars,
            decision_timeframe=definition.timeframe,
        )
        clipped_start, clipped_end = _clip_evaluation_window(
            clipped_start, clipped_end, clip_start, clip_end
        )
    return _clip_additional_extra_tf_window(
        clipped_start, clipped_end, binding, strategy, dataset_store
    )


def _clip_additional_extra_tf_window(
    start: datetime,
    end: datetime,
    binding: AdditionalInstrumentDataset,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> tuple[datetime, datetime]:
    """Clip omitted bounds to one extra product's extra-TF datasets."""
    groups = dict(extra_indicator_timeframe_groups(strategy.definition))
    clipped_start, clipped_end = start, end
    for clock in binding.indicator_dataset_fingerprints:
        clock_manifest = _load_bound_manifest(
            dataset_store,
            clock.dataset_fingerprint,
            missing_message=(
                "The selected additional-instrument extra-TF dataset was not found or "
                "is not a verified complete artifact."
            ),
        )
        clip_start, clip_end = _closed_bar_clip_from_manifest(
            clock_manifest,
            clock_timeframe=clock.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                groups[clock.timeframe], operands=strategy_indicator_operands(strategy.definition)
            ),
            decision_timeframe=strategy.definition.timeframe,
        )
        clipped_start, clipped_end = _clip_evaluation_window(
            clipped_start, clipped_end, clip_start, clip_end
        )
    return clipped_start, clipped_end
