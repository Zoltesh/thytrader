"""Dataset coverage checks of a backtest submission's evaluation window.

Each bound dataset (HTF, extra timeframe, additional instrument, reference) must be
a complete artifact for the declared product and clock that covers the closed bars
the window needs; failures are caller-input rejections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.backtest.submission_bindings import (
    _require_additional_product_order,
    _require_reference_dataset_request,
)
from thytrader.backtest.submission_models import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
)
from thytrader.evaluation.models import (
    AdditionalInstrumentDataset,
    ReferenceInstrumentDataset,
    warmup_starts_at,
)
from thytrader.evaluation.multi_timeframe import closed_bar_required_coverage, htf_required_coverage
from thytrader.market_data.datasets import DatasetManifest, DatasetStoreError
from thytrader.market_data.models import parse_candle_interval
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    reference_data_requirements,
    strategy_indicator_operands,
    unbound_indicator_timeframes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.strategies.models import (
        IndicatorDefinition,
        IndicatorOperand,
        StrategyDefinition,
    )
    from thytrader.strategies.snapshots import StrategySnapshot


def _require_coverage_windows(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> None:
    """Reject a filled window that extra-clock, extra-product, or reference data cannot cover."""
    _require_htf_window(request, strategy, dataset_store)
    _require_indicator_timeframe_window(request, strategy, dataset_store)
    _require_additional_instrument_window(request, strategy, dataset_store)
    _require_reference_window(request, strategy, dataset_store)


def _reference_missing_message(binding: ReferenceInstrumentDataset) -> str:
    """Name one reference binding whose dataset artifact is absent or unverified."""
    return (
        f"The reference-instrument dataset for {binding.reference_id} ({binding.product_id} "
        f"{binding.timeframe}) was not found or is not a verified complete artifact."
    )


def _require_reference_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> None:
    """Confirm each reference dataset covers its last-completed bars for the window."""
    definition = strategy.definition
    _require_reference_dataset_request(request, definition)
    evaluation_start, evaluation_end = _filled_window(request)
    for binding, requirement in zip(
        request.reference_dataset_fingerprints,
        reference_data_requirements(definition),
        strict=True,
    ):
        manifest = _load_bound_manifest(
            dataset_store,
            binding.dataset_fingerprint,
            missing_message=_reference_missing_message(binding),
        )
        label = f"{binding.reference_id} ({requirement.product_id} {requirement.timeframe})"
        if (
            manifest.provider != "coinbase"
            or manifest.product_id != requirement.product_id
            or manifest.timeframe != requirement.timeframe
        ):
            raise BacktestSubmissionRejectedError(
                f"The reference-instrument dataset for {label} is {manifest.product_id} "
                f"{manifest.timeframe} ({manifest.provider}); it must match the declared "
                "reference product and timeframe."
            )
        if not manifest.complete:
            raise BacktestSubmissionRejectedError(
                f"The reference-instrument dataset for {label} must be complete."
            )
        required_start, required_end = closed_bar_required_coverage(
            evaluation_starts_at=evaluation_start,
            evaluation_ends_at=evaluation_end,
            timeframe=requirement.timeframe,
            warmup_bars=requirement.warmup_bars,
        )
        starts_at = _manifest_instant(manifest.starts_at)
        ends_at = _manifest_instant(manifest.ends_at)
        if starts_at > required_start or ends_at < required_end:
            raise BacktestSubmissionRejectedError(
                f"Requested evaluation window is not fully covered by the reference-instrument "
                f"dataset for {label}: it needs closed bars from {required_start.isoformat()} "
                f"to {required_end.isoformat()} ({requirement.warmup_bars} warmup bars)."
            )


def _load_bound_manifest(
    dataset_store: DatasetStore,
    fingerprint: str,
    *,
    missing_message: str,
) -> DatasetManifest:
    """Load one dataset manifest or reject the fingerprint as caller input."""
    try:
        return dataset_store.load_manifest(fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(missing_message) from error


def _manifest_instant(value: str) -> datetime:
    """Parse one dataset coverage timestamp as timezone-aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Dataset coverage timestamps are invalid.")
    return parsed.astimezone(UTC)


def _filled_window(request: BacktestSubmissionRequest) -> tuple[datetime, datetime]:
    """Return evaluation bounds after optional dataset fill."""
    if request.evaluation_start is None or request.evaluation_end is None:
        raise BacktestSubmissionError("Backtest submission is unavailable.")
    return request.evaluation_start, request.evaluation_end


def _require_htf_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> None:
    """Confirm the HTF dataset covers last-completed HTF bars for the LTF window."""
    definition = strategy.definition
    htf_filter = definition.htf_filter
    if htf_filter is None:
        if request.htf_dataset_fingerprint is not None:
            raise BacktestSubmissionRejectedError(
                "htf_dataset_fingerprint is only valid when the strategy declares htf_filter."
            )
        return
    if request.htf_dataset_fingerprint is None:
        raise BacktestSubmissionRejectedError(
            "Multi-timeframe strategies require htf_dataset_fingerprint."
        )
    try:
        htf_manifest = dataset_store.load_manifest(request.htf_dataset_fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected HTF dataset was not found or is not a verified complete artifact."
        ) from error
    if htf_manifest.product_id != definition.instrument.product_id:
        raise BacktestSubmissionRejectedError(
            "HTF dataset product_id must match the published strategy instrument."
        )
    if htf_manifest.timeframe != htf_filter.timeframe:
        raise BacktestSubmissionRejectedError(
            "HTF dataset timeframe must match strategy htf_filter.timeframe."
        )
    if not htf_manifest.complete:
        raise BacktestSubmissionRejectedError("HTF dataset status must be complete.")
    evaluation_start, evaluation_end = _filled_window(request)
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=evaluation_start,
        evaluation_ends_at=evaluation_end,
        htf_filter=htf_filter,
    )
    htf_starts_at = _manifest_instant(htf_manifest.starts_at)
    htf_ends_at = _manifest_instant(htf_manifest.ends_at)
    if htf_starts_at > required_start or htf_ends_at < required_end:
        raise BacktestSubmissionRejectedError(
            "Requested evaluation window is not fully covered by the HTF dataset."
        )


def _require_indicator_timeframe_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> None:
    """Confirm extra-TF datasets cover last-completed bars for the LTF window."""
    definition = strategy.definition
    required = unbound_indicator_timeframes(definition)
    declared = tuple(item.timeframe for item in request.indicator_dataset_fingerprints)
    if declared != required:
        raise BacktestSubmissionRejectedError(
            "indicator_dataset_fingerprints must match the strategy extra indicator timeframes."
        )
    groups = dict(extra_indicator_timeframe_groups(definition))
    evaluation_start, evaluation_end = _filled_window(request)
    for binding in request.indicator_dataset_fingerprints:
        try:
            manifest = dataset_store.load_manifest(binding.dataset_fingerprint)
        except DatasetStoreError as error:
            raise BacktestSubmissionRejectedError(
                "The selected indicator-timeframe dataset was not found or is not a "
                "verified complete artifact."
            ) from error
        if manifest.product_id != definition.instrument.product_id:
            raise BacktestSubmissionRejectedError(
                "Indicator-timeframe dataset product_id must match the published strategy."
            )
        if manifest.timeframe != binding.timeframe:
            raise BacktestSubmissionRejectedError(
                "Indicator-timeframe dataset must match the declared indicator timeframe."
            )
        if not manifest.complete:
            raise BacktestSubmissionRejectedError(
                "Indicator-timeframe dataset status must be complete."
            )
        required_start, required_end = closed_bar_required_coverage(
            evaluation_starts_at=evaluation_start,
            evaluation_ends_at=evaluation_end,
            timeframe=binding.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                groups[binding.timeframe], operands=strategy_indicator_operands(definition)
            ),
        )
        starts_at = _manifest_instant(manifest.starts_at)
        ends_at = _manifest_instant(manifest.ends_at)
        if starts_at > required_start or ends_at < required_end:
            raise BacktestSubmissionRejectedError(
                "Requested evaluation window is not fully covered by the indicator-timeframe "
                "dataset."
            )


def _require_additional_instrument_window(
    request: BacktestSubmissionRequest,
    strategy: StrategySnapshot,
    dataset_store: DatasetStore,
) -> None:
    """Confirm extra product datasets cover the same evaluation window as the primary."""
    definition = strategy.definition
    if not definition.additional_instruments and not request.additional_instrument_datasets:
        return
    _require_additional_product_order(request, definition)
    required_clocks = unbound_indicator_timeframes(definition)
    groups = dict(extra_indicator_timeframe_groups(definition))
    evaluation_start, evaluation_end = _filled_window(request)
    interval = parse_candle_interval(definition.timeframe)
    warmup_start = warmup_starts_at(
        evaluation_start,
        definition.data_requirements.warmup_bars,
        definition.timeframe,
    )
    required_fill_end = evaluation_end + interval.duration
    for binding in request.additional_instrument_datasets:
        _require_additional_ltf_window(
            binding,
            definition=definition,
            dataset_store=dataset_store,
            warmup_start=warmup_start,
            required_fill_end=required_fill_end,
        )
        _require_additional_htf_window(
            binding,
            definition=definition,
            dataset_store=dataset_store,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        )
        _require_additional_extra_tf_window(
            binding,
            dataset_store=dataset_store,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            required_clocks=required_clocks,
            groups=groups,
            operands=strategy_indicator_operands(definition),
        )


def _require_additional_ltf_window(
    binding: AdditionalInstrumentDataset,
    *,
    definition: StrategyDefinition,
    dataset_store: DatasetStore,
    warmup_start: datetime,
    required_fill_end: datetime,
) -> None:
    """Confirm one extra product's decision-clock dataset covers the evaluation window."""
    try:
        manifest = dataset_store.load_manifest(binding.dataset_fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected additional-instrument dataset was not found or is not a "
            "verified complete artifact."
        ) from error
    if (
        manifest.product_id != binding.product_id
        or manifest.timeframe != definition.timeframe
        or not manifest.complete
    ):
        raise BacktestSubmissionRejectedError(
            "Additional-instrument dataset must be a complete artifact for that product "
            "and decision clock."
        )
    starts_at = _manifest_instant(manifest.starts_at)
    ends_at = _manifest_instant(manifest.ends_at)
    if starts_at > warmup_start or ends_at < required_fill_end:
        raise BacktestSubmissionRejectedError(
            "Requested evaluation window is not fully covered by an additional-instrument dataset."
        )


def _require_additional_htf_window(
    binding: AdditionalInstrumentDataset,
    *,
    definition: StrategyDefinition,
    dataset_store: DatasetStore,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> None:
    """Confirm one extra product's HTF dataset when the strategy declares a filter."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return
    if binding.htf_dataset_fingerprint is None:
        raise BacktestSubmissionRejectedError(
            "additional_instrument_datasets require htf_dataset_fingerprint when the "
            "strategy declares htf_filter."
        )
    try:
        htf_manifest = dataset_store.load_manifest(binding.htf_dataset_fingerprint)
    except DatasetStoreError as error:
        raise BacktestSubmissionRejectedError(
            "The selected additional-instrument HTF dataset was not found or is not a "
            "verified complete artifact."
        ) from error
    if (
        htf_manifest.product_id != binding.product_id
        or htf_manifest.timeframe != htf_filter.timeframe
        or not htf_manifest.complete
    ):
        raise BacktestSubmissionRejectedError(
            "Additional-instrument HTF dataset must match that product and HTF clock."
        )
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=evaluation_start,
        evaluation_ends_at=evaluation_end,
        htf_filter=htf_filter,
    )
    htf_starts_at = _manifest_instant(htf_manifest.starts_at)
    htf_ends_at = _manifest_instant(htf_manifest.ends_at)
    if htf_starts_at > required_start or htf_ends_at < required_end:
        raise BacktestSubmissionRejectedError(
            "Requested evaluation window is not fully covered by an additional-instrument "
            "HTF dataset."
        )


def _require_additional_extra_tf_window(
    binding: AdditionalInstrumentDataset,
    *,
    dataset_store: DatasetStore,
    evaluation_start: datetime,
    evaluation_end: datetime,
    required_clocks: tuple[str, ...],
    groups: Mapping[str, tuple[IndicatorDefinition, ...]],
    operands: tuple[IndicatorOperand, ...],
) -> None:
    """Confirm extra-TF datasets for one extra product match the published clocks."""
    clocks = tuple(item.timeframe for item in binding.indicator_dataset_fingerprints)
    if clocks != required_clocks:
        raise BacktestSubmissionRejectedError(
            "additional-instrument extra-TF fingerprints must match the strategy clocks."
        )
    for clock in binding.indicator_dataset_fingerprints:
        try:
            clock_manifest = dataset_store.load_manifest(clock.dataset_fingerprint)
        except DatasetStoreError as error:
            raise BacktestSubmissionRejectedError(
                "The selected additional-instrument extra-TF dataset was not found or is "
                "not a verified complete artifact."
            ) from error
        if (
            clock_manifest.product_id != binding.product_id
            or clock_manifest.timeframe != clock.timeframe
            or not clock_manifest.complete
        ):
            raise BacktestSubmissionRejectedError(
                "Additional-instrument extra-TF dataset must match that product and clock."
            )
        required_start, required_end = closed_bar_required_coverage(
            evaluation_starts_at=evaluation_start,
            evaluation_ends_at=evaluation_end,
            timeframe=clock.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                groups[clock.timeframe], operands=operands
            ),
        )
        clock_start = _manifest_instant(clock_manifest.starts_at)
        clock_end = _manifest_instant(clock_manifest.ends_at)
        if clock_start > required_start or clock_end < required_end:
            raise BacktestSubmissionRejectedError(
                "Requested evaluation window is not fully covered by an additional-instrument "
                "extra-TF dataset."
            )
