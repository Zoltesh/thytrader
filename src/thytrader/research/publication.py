"""Publication and eligibility contracts for immutable research-run specifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.research.models import warmup_starts_at
from thytrader.research.multi_timeframe import closed_bar_required_coverage, htf_required_coverage
from thytrader.strategies.models import (
    StrategyStatus,
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    lockstep_product_ids,
    unbound_indicator_timeframes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.market_data.datasets import DatasetManifest
    from thytrader.research.models import AdditionalInstrumentDataset, ResearchRunSpecification
    from thytrader.strategies.models import IndicatorDefinition, StrategyDefinition
    from thytrader.strategies.publication import PublishedStrategy


class ResearchRunPublicationError(RuntimeError):
    """Report a redacted run-spec publication or artifact-integrity failure."""


@dataclass(frozen=True, slots=True)
class PublishedResearchRunSpecification:
    """A verified immutable research request addressed by content fingerprint."""

    run_fingerprint: str
    specification: ResearchRunSpecification


def verify_research_run_eligibility(
    specification: ResearchRunSpecification,
    published_strategy: PublishedStrategy,
    manifest: DatasetManifest,
    htf_manifest: DatasetManifest | None = None,
    indicator_manifests: dict[str, DatasetManifest] | None = None,
    additional_manifests: dict[str, DatasetManifest] | None = None,
    additional_htf_manifests: dict[str, DatasetManifest] | None = None,
    additional_indicator_manifests: dict[str, dict[str, DatasetManifest]] | None = None,
) -> None:
    """Fail closed unless exact verified artifacts cover the complete run contract."""
    definition = published_strategy.definition
    if (
        specification.strategy_fingerprint != published_strategy.strategy_fingerprint
        or definition.status is not StrategyStatus.PUBLISHED
    ):
        raise ResearchRunPublicationError(
            "Research run strategy identity does not match the verified published strategy."
        )
    _require_decision_dataset(specification, definition, manifest)
    if specification.warmup.bars != definition.data_requirements.warmup_bars:
        raise ResearchRunPublicationError(
            "Research run warmup bars do not match the published strategy requirement."
        )

    try:
        dataset_starts_at = _parse_canonical_utc(manifest.starts_at)
        dataset_ends_at = _parse_canonical_utc(manifest.ends_at)
    except ValueError as error:
        raise ResearchRunPublicationError(
            "Research run dataset coverage timestamps are invalid."
        ) from error
    if dataset_starts_at > specification.warmup.starts_at:
        raise ResearchRunPublicationError(
            "Research run dataset does not provide the required warmup coverage."
        )
    if not evaluation_end_fits_dataset(
        dataset_starts_at=dataset_starts_at,
        dataset_ends_at=dataset_ends_at,
        evaluation_start=specification.evaluation.starts_at,
        evaluation_end=specification.evaluation.ends_at,
        warmup_bars=definition.data_requirements.warmup_bars,
        timeframe=definition.timeframe,
    ):
        raise ResearchRunPublicationError(
            evaluation_window_suggestion(
                dataset_starts_at=dataset_starts_at,
                dataset_ends_at=dataset_ends_at,
                warmup_bars=definition.data_requirements.warmup_bars,
                timeframe=definition.timeframe,
            )
        )
    _require_htf_dataset(specification, definition, htf_manifest)
    _require_indicator_timeframe_datasets(specification, definition, indicator_manifests or {})
    _require_additional_instrument_datasets(
        specification,
        definition,
        additional_manifests=additional_manifests or {},
        additional_htf_manifests=additional_htf_manifests or {},
        additional_indicator_manifests=additional_indicator_manifests or {},
    )


def _require_decision_dataset(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    manifest: DatasetManifest,
) -> None:
    """Require the primary dataset to match the published LTF decision clock."""
    if (
        specification.dataset_fingerprint != manifest.content_fingerprint
        or not manifest.complete
        or manifest.provider != "coinbase"
        or manifest.product_id != definition.instrument.product_id
        or manifest.timeframe != definition.timeframe
    ):
        if not manifest.complete:
            raise ResearchRunPublicationError(
                "Research run dataset must be a verified complete immutable artifact."
            )
        raise ResearchRunPublicationError(
            "Research run dataset identity does not match the verified strategy and request."
        )


def _require_htf_dataset(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    htf_manifest: DatasetManifest | None,
) -> None:
    """Require an HTF dataset fingerprint and coverage iff the strategy declares a filter."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        if specification.htf_dataset_fingerprint is not None:
            raise ResearchRunPublicationError(
                "Research run HTF dataset is not declared by the published strategy."
            )
        return
    if specification.htf_dataset_fingerprint is None or htf_manifest is None:
        raise ResearchRunPublicationError(
            "Research run HTF dataset fingerprint is required for an HTF-filter strategy."
        )
    if (
        specification.htf_dataset_fingerprint != htf_manifest.content_fingerprint
        or not htf_manifest.complete
        or htf_manifest.provider != "coinbase"
        or htf_manifest.product_id != definition.instrument.product_id
        or htf_manifest.timeframe != htf_filter.timeframe
    ):
        raise ResearchRunPublicationError(
            "Research run HTF dataset identity does not match the verified strategy and request."
        )
    try:
        htf_starts_at = _parse_canonical_utc(htf_manifest.starts_at)
        htf_ends_at = _parse_canonical_utc(htf_manifest.ends_at)
        required_start, required_end = htf_required_coverage(
            evaluation_starts_at=specification.evaluation.starts_at,
            evaluation_ends_at=specification.evaluation.ends_at,
            htf_filter=htf_filter,
        )
    except ValueError as error:
        raise ResearchRunPublicationError(
            "Research run HTF dataset coverage timestamps are invalid."
        ) from error
    if htf_starts_at > required_start or htf_ends_at < required_end:
        raise ResearchRunPublicationError(
            "Research run HTF dataset does not provide the required closed-bar coverage."
        )


def _require_indicator_timeframe_datasets(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    indicator_manifests: dict[str, DatasetManifest],
) -> None:
    """Require extra-TF datasets iff the strategy declares unbound indicator clocks."""
    required = unbound_indicator_timeframes(definition)
    declared = tuple(item.timeframe for item in specification.indicator_dataset_fingerprints)
    if declared != required:
        raise ResearchRunPublicationError(
            "Research run indicator-timeframe datasets do not match the published strategy."
        )
    groups = dict(extra_indicator_timeframe_groups(definition))
    for binding in specification.indicator_dataset_fingerprints:
        manifest = indicator_manifests.get(binding.timeframe)
        if (
            manifest is None
            or binding.dataset_fingerprint != manifest.content_fingerprint
            or not manifest.complete
            or manifest.provider != "coinbase"
            or manifest.product_id != definition.instrument.product_id
            or manifest.timeframe != binding.timeframe
        ):
            raise ResearchRunPublicationError(
                "Research run indicator-timeframe dataset identity does not match the "
                "verified strategy and request."
            )
        try:
            starts_at = _parse_canonical_utc(manifest.starts_at)
            ends_at = _parse_canonical_utc(manifest.ends_at)
            required_start, required_end = closed_bar_required_coverage(
                evaluation_starts_at=specification.evaluation.starts_at,
                evaluation_ends_at=specification.evaluation.ends_at,
                timeframe=binding.timeframe,
                warmup_bars=extra_indicator_timeframe_warmup(groups[binding.timeframe]),
            )
        except ValueError as error:
            raise ResearchRunPublicationError(
                "Research run indicator-timeframe dataset coverage timestamps are invalid."
            ) from error
        if starts_at > required_start or ends_at < required_end:
            raise ResearchRunPublicationError(
                "Research run indicator-timeframe dataset does not provide the required "
                "closed-bar coverage."
            )


def _require_additional_instrument_datasets(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    *,
    additional_manifests: dict[str, DatasetManifest],
    additional_htf_manifests: dict[str, DatasetManifest],
    additional_indicator_manifests: dict[str, dict[str, DatasetManifest]],
) -> None:
    """Require extra product datasets iff the strategy covers more than the primary."""
    extra_products = tuple(
        product_id
        for product_id in lockstep_product_ids(definition)
        if product_id != definition.instrument.product_id
    )
    declared = tuple(item.product_id for item in specification.additional_instrument_datasets)
    if declared != extra_products:
        raise ResearchRunPublicationError(
            "Research run additional_instrument_datasets must match extra covered products."
        )
    required_extra_clocks = unbound_indicator_timeframes(definition)
    groups = dict(extra_indicator_timeframe_groups(definition))
    for binding in specification.additional_instrument_datasets:
        _require_additional_ltf_coverage(
            specification,
            definition,
            binding,
            additional_manifests=additional_manifests,
        )
        _require_additional_htf_coverage(
            specification,
            definition,
            binding,
            additional_htf_manifests=additional_htf_manifests,
        )
        _require_additional_extra_tf_coverage(
            specification,
            binding,
            additional_indicator_manifests=additional_indicator_manifests,
            required_extra_clocks=required_extra_clocks,
            groups=groups,
        )


def _require_additional_ltf_coverage(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    binding: AdditionalInstrumentDataset,
    *,
    additional_manifests: dict[str, DatasetManifest],
) -> None:
    """Require a complete decision-clock dataset for one extra covered product."""
    manifest = additional_manifests.get(binding.product_id)
    if (
        manifest is None
        or binding.dataset_fingerprint != manifest.content_fingerprint
        or not manifest.complete
        or manifest.provider != "coinbase"
        or manifest.product_id != binding.product_id
        or manifest.timeframe != definition.timeframe
    ):
        raise ResearchRunPublicationError(
            "Research run additional-instrument dataset identity does not match the "
            "verified strategy and request."
        )
    try:
        dataset_starts_at = _parse_canonical_utc(manifest.starts_at)
        dataset_ends_at = _parse_canonical_utc(manifest.ends_at)
        required_fill_end = (
            specification.evaluation.ends_at + parse_candle_interval(definition.timeframe).duration
        )
    except (OverflowError, ValueError) as error:
        raise ResearchRunPublicationError(
            "Research run additional-instrument dataset coverage timestamps are invalid."
        ) from error
    if dataset_starts_at > specification.warmup.starts_at or dataset_ends_at < required_fill_end:
        raise ResearchRunPublicationError(
            "Research run additional-instrument dataset does not provide required coverage."
        )


def _require_additional_htf_coverage(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    binding: AdditionalInstrumentDataset,
    *,
    additional_htf_manifests: dict[str, DatasetManifest],
) -> None:
    """Require HTF coverage for one extra product iff the strategy declares a filter."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        if binding.htf_dataset_fingerprint is not None:
            raise ResearchRunPublicationError(
                "Research run additional-instrument HTF dataset is not declared by the "
                "published strategy."
            )
        return
    htf_manifest = additional_htf_manifests.get(binding.product_id)
    if binding.htf_dataset_fingerprint is None or htf_manifest is None:
        raise ResearchRunPublicationError(
            "Research run additional-instrument HTF dataset fingerprint is required."
        )
    if (
        binding.htf_dataset_fingerprint != htf_manifest.content_fingerprint
        or not htf_manifest.complete
        or htf_manifest.provider != "coinbase"
        or htf_manifest.product_id != binding.product_id
        or htf_manifest.timeframe != htf_filter.timeframe
    ):
        raise ResearchRunPublicationError(
            "Research run additional-instrument HTF dataset identity does not match."
        )
    try:
        htf_starts_at = _parse_canonical_utc(htf_manifest.starts_at)
        htf_ends_at = _parse_canonical_utc(htf_manifest.ends_at)
        required_start, required_end = htf_required_coverage(
            evaluation_starts_at=specification.evaluation.starts_at,
            evaluation_ends_at=specification.evaluation.ends_at,
            htf_filter=htf_filter,
        )
    except ValueError as error:
        raise ResearchRunPublicationError(
            "Research run additional-instrument HTF coverage timestamps are invalid."
        ) from error
    if htf_starts_at > required_start or htf_ends_at < required_end:
        raise ResearchRunPublicationError(
            "Research run additional-instrument HTF dataset does not provide required "
            "closed-bar coverage."
        )


def _require_additional_extra_tf_coverage(
    specification: ResearchRunSpecification,
    binding: AdditionalInstrumentDataset,
    *,
    additional_indicator_manifests: dict[str, dict[str, DatasetManifest]],
    required_extra_clocks: tuple[str, ...],
    groups: Mapping[str, tuple[IndicatorDefinition, ...]],
) -> None:
    """Require extra-TF datasets for one extra product iff the strategy declares those clocks."""
    declared_clocks = tuple(item.timeframe for item in binding.indicator_dataset_fingerprints)
    if declared_clocks != required_extra_clocks:
        raise ResearchRunPublicationError(
            "Research run additional-instrument extra-TF datasets do not match the "
            "published strategy."
        )
    product_clocks = additional_indicator_manifests.get(binding.product_id, {})
    for clock in binding.indicator_dataset_fingerprints:
        clock_manifest = product_clocks.get(clock.timeframe)
        if (
            clock_manifest is None
            or clock.dataset_fingerprint != clock_manifest.content_fingerprint
            or not clock_manifest.complete
            or clock_manifest.provider != "coinbase"
            or clock_manifest.product_id != binding.product_id
            or clock_manifest.timeframe != clock.timeframe
        ):
            raise ResearchRunPublicationError(
                "Research run additional-instrument extra-TF dataset identity does not match."
            )
        try:
            clock_start = _parse_canonical_utc(clock_manifest.starts_at)
            clock_end = _parse_canonical_utc(clock_manifest.ends_at)
            required_start, required_end = closed_bar_required_coverage(
                evaluation_starts_at=specification.evaluation.starts_at,
                evaluation_ends_at=specification.evaluation.ends_at,
                timeframe=clock.timeframe,
                warmup_bars=extra_indicator_timeframe_warmup(groups[clock.timeframe]),
            )
        except ValueError as error:
            raise ResearchRunPublicationError(
                "Research run additional-instrument extra-TF coverage timestamps are invalid."
            ) from error
        if clock_start > required_start or clock_end < required_end:
            raise ResearchRunPublicationError(
                "Research run additional-instrument extra-TF dataset does not provide "
                "required closed-bar coverage."
            )


def latest_allowed_evaluation_end(
    *,
    dataset_ends_at: datetime,
    timeframe: str,
) -> datetime:
    """Return the latest half-open ``evaluation_end`` one complete dataset supports.

    ``dataset_ends_at`` is the last complete candle open. The final next-open fill
    occurs on that candle, so ``evaluation_end`` may equal this bound inclusively.
    """
    interval = parse_candle_interval(timeframe)
    return dataset_ends_at - interval.duration


def evaluation_end_fits_dataset(
    *,
    dataset_starts_at: datetime,
    dataset_ends_at: datetime,
    evaluation_start: datetime,
    evaluation_end: datetime,
    warmup_bars: int,
    timeframe: str,
) -> bool:
    """Return whether one half-open evaluation window fits verified dataset coverage."""
    interval = parse_candle_interval(timeframe)
    latest = latest_allowed_evaluation_end(dataset_ends_at=dataset_ends_at, timeframe=timeframe)
    if evaluation_end > latest:
        return False
    warmup_start = warmup_starts_at(evaluation_start, warmup_bars, timeframe)
    if dataset_starts_at > warmup_start:
        return False
    required_fill_end = evaluation_end + interval.duration
    coverage_end_exclusive = dataset_ends_at + interval.duration
    return required_fill_end <= coverage_end_exclusive


def dataset_evaluation_bounds(
    *,
    dataset_starts_at: datetime,
    dataset_ends_at: datetime,
    warmup_bars: int,
    timeframe: str,
) -> tuple[datetime, datetime]:
    """Return the half-open evaluation window one complete dataset can support.

    Warmup bars must sit before the window and one extra bar after ``evaluation_end``
    is required for the final next-open fill.
    """
    interval = parse_candle_interval(timeframe)
    evaluation_start = dataset_starts_at + interval.duration * warmup_bars
    evaluation_end = latest_allowed_evaluation_end(
        dataset_ends_at=dataset_ends_at,
        timeframe=timeframe,
    )
    if evaluation_start >= evaluation_end:
        raise ResearchRunPublicationError(
            "The selected dataset is too short for this strategy's warmup and next-open fill. "
            f"Need at least {warmup_bars + 2} {timeframe} bars."
        )
    return evaluation_start, evaluation_end


def evaluation_window_suggestion(
    *,
    dataset_starts_at: datetime,
    dataset_ends_at: datetime,
    warmup_bars: int,
    timeframe: str,
) -> str:
    """Explain a rejected window and suggest a usable ISO range."""
    try:
        start, end = dataset_evaluation_bounds(
            dataset_starts_at=dataset_starts_at,
            dataset_ends_at=dataset_ends_at,
            warmup_bars=warmup_bars,
            timeframe=timeframe,
        )
    except ResearchRunPublicationError as error:
        return str(error)
    latest = _canonical_utc_text(end)
    dataset_end = _canonical_utc_text(dataset_ends_at)
    suggested_start = _canonical_utc_text(start)
    return (
        "The evaluation window does not fit the selected dataset. "
        f"evaluation_end uses a half-open interval [evaluation_start, evaluation_end); "
        f"the latest allowed evaluation_end is {latest} "
        f"(last complete candle open {dataset_end} minus one {timeframe} bar). "
        f"Suggested range: {suggested_start} to {latest}."
    )


def explain_evaluation_window_rejection(
    *,
    dataset_starts_at: datetime,
    dataset_ends_at: datetime,
    evaluation_start: datetime,
    evaluation_end: datetime,
    warmup_bars: int,
    timeframe: str,
) -> str | None:
    """Name the field that failed dataset coverage, or None when the window fits."""
    suggestion = evaluation_window_suggestion(
        dataset_starts_at=dataset_starts_at,
        dataset_ends_at=dataset_ends_at,
        warmup_bars=warmup_bars,
        timeframe=timeframe,
    )
    if evaluation_end_fits_dataset(
        dataset_starts_at=dataset_starts_at,
        dataset_ends_at=dataset_ends_at,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        warmup_bars=warmup_bars,
        timeframe=timeframe,
    ):
        return None
    interval = parse_candle_interval(timeframe)
    latest = latest_allowed_evaluation_end(dataset_ends_at=dataset_ends_at, timeframe=timeframe)
    warmup_start = warmup_starts_at(evaluation_start, warmup_bars, timeframe)
    if evaluation_end > latest:
        return (
            f"evaluation_end {_canonical_utc_text(evaluation_end)} is after the latest allowed "
            f"evaluation_end {_canonical_utc_text(latest)}. {suggestion}"
        )
    if dataset_starts_at > warmup_start:
        return (
            f"evaluation_start {_canonical_utc_text(evaluation_start)} requires warmup coverage "
            f"starting at {_canonical_utc_text(warmup_start)}; dataset starts at "
            f"{_canonical_utc_text(dataset_starts_at)}. {suggestion}"
        )
    required_fill_end = evaluation_end + interval.duration
    coverage_end_exclusive = dataset_ends_at + interval.duration
    if required_fill_end > coverage_end_exclusive:
        return (
            f"evaluation_end {_canonical_utc_text(evaluation_end)} requires a next-open fill at "
            f"{_canonical_utc_text(required_fill_end)}; dataset coverage ends at "
            f"{_canonical_utc_text(dataset_ends_at)}. {suggestion}"
        )
    return suggestion


def _canonical_utc_text(value: datetime) -> str:
    """Serialize one UTC instant with the canonical Z suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_canonical_utc(value: str) -> datetime:
    """Parse the canonical UTC timestamps supplied by a verified dataset manifest."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat().replace("+00:00", "Z") != value
    ):
        raise ValueError("timestamp is not canonical UTC")
    return parsed
