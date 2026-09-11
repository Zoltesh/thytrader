"""Publication and eligibility contracts for immutable research-run specifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.strategies.models import StrategyStatus

if TYPE_CHECKING:
    from thytrader.market_data.datasets import DatasetManifest
    from thytrader.research.models import ResearchRunSpecification
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
) -> None:
    """Fail closed unless exact verified artifacts cover the complete run contract."""
    if (
        specification.strategy_fingerprint != published_strategy.strategy_fingerprint
        or published_strategy.definition.status is not StrategyStatus.PUBLISHED
    ):
        raise ResearchRunPublicationError(
            "Research run strategy identity does not match the verified published strategy."
        )
    if (
        specification.dataset_fingerprint != manifest.content_fingerprint
        or not manifest.complete
        or manifest.provider != "coinbase"
        or manifest.product_id != published_strategy.definition.instrument.product_id
        or manifest.timeframe != published_strategy.definition.timeframe
    ):
        if not manifest.complete:
            raise ResearchRunPublicationError(
                "Research run dataset must be a verified complete immutable artifact."
            )
        raise ResearchRunPublicationError(
            "Research run dataset identity does not match the verified strategy and request."
        )
    if specification.warmup.bars != published_strategy.definition.data_requirements.warmup_bars:
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
    interval = parse_candle_interval(published_strategy.definition.timeframe)
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
