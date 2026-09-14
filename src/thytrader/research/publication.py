"""Publication and eligibility contracts for immutable research-run specifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.research.multi_timeframe import htf_required_coverage
from thytrader.strategies.models import StrategyStatus

if TYPE_CHECKING:
    from thytrader.market_data.datasets import DatasetManifest
    from thytrader.research.models import ResearchRunSpecification
    from thytrader.strategies.models import StrategyDefinition
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
    interval = parse_candle_interval(definition.timeframe)
    try:
        required_fill_end = specification.evaluation.ends_at + interval.duration
    except OverflowError as error:
        raise ResearchRunPublicationError(
            "Research run evaluation window cannot represent required next-candle-open coverage."
        ) from error
    if dataset_ends_at < required_fill_end:
        raise ResearchRunPublicationError(
            "Research run dataset lacks next-candle-open coverage for the final evaluation candle."
        )
    _require_htf_dataset(specification, definition, htf_manifest)


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


def dataset_evaluation_bounds(
    *,
    dataset_starts_at: datetime,
    dataset_ends_at: datetime,
    warmup_bars: int,
    timeframe: str,
) -> tuple[datetime, datetime]:
    """Return the inclusive evaluation window one complete dataset can support.

    Warmup bars must sit before the window and one extra bar after it is required
    for the final next-open fill.
    """
    interval = parse_candle_interval(timeframe)
    evaluation_start = dataset_starts_at + interval.duration * warmup_bars
    evaluation_end = dataset_ends_at - interval.duration
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
    return (
        "The evaluation window does not fit the selected dataset. "
        f"evaluation_end must be at or before {end.isoformat()} "
        f"(dataset end minus one {timeframe} bar). "
        f"Suggested range: {start.isoformat()} to {end.isoformat()}."
    )


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
