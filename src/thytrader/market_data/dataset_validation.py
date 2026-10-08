"""Fail-closed validation of dataset identifiers, timeframes, paths, and range reports.

Every check raises :class:`~thytrader.market_data.dataset_manifest.DatasetStoreError`
before a report can publish files or a manifest path can be resolved beneath its root.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from thytrader.market_data.dataset_manifest import DatasetStoreError
from thytrader.market_data.models import interval_from_range, parse_candle_interval
from thytrader.market_data.quality import CandleQualityError, analyze_range

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.market_data.models import CandleInterval, CandleRangeReport


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _validate_identifier(value: str) -> None:
    """Reject filesystem-unsafe provider and product identifier values."""
    if not _IDENTIFIER.fullmatch(value):
        message = "Dataset identifier contains unsafe filesystem characters."
        raise DatasetStoreError(message)


def _require_timeframe(timeframe: str) -> CandleInterval:
    """Parse a supported dataset timeframe or fail closed."""
    try:
        return parse_candle_interval(timeframe)
    except ValueError as error:
        message = "Dataset verification failed because the timeframe is unsupported."
        raise DatasetStoreError(message) from error


def _validate_report_for_publication(report: CandleRangeReport) -> None:
    """Recompute every durable range fact before a report can publish dataset files."""
    if not report.complete:
        message = "Only a complete historical range can be persisted as a dataset."
        raise DatasetStoreError(message)
    if not report.quality.candles:
        message = "A complete dataset must contain at least one candle."
        raise DatasetStoreError(message)
    try:
        interval = interval_from_range(report)
        recomputed_report = analyze_range(
            tuple(report.quality.candles),
            interval,
            report.starts_at,
            report.ends_at,
            report.ends_at + interval.duration,
        )
    except CandleQualityError as error:
        raise DatasetStoreError(str(error)) from error
    except (OverflowError, TypeError, ValueError) as error:
        message = "Dataset range report facts are invalid and cannot be published."
        raise DatasetStoreError(message) from error
    if not _report_facts_match(report, recomputed_report):
        message = "Dataset range report facts are invalid and cannot be published."
        raise DatasetStoreError(message)


def _safe_dataset_path(root: Path, relative: str) -> Path:
    """Resolve a manifest-relative path only when it remains safely beneath the dataset root."""
    if (
        not relative
        or relative.startswith(("/", "\\"))
        or "\\" in relative
        or ".." in relative.split("/")
    ):
        message = "Dataset verification failed because a manifest file path escapes its root."
        raise DatasetStoreError(message)
    candidate = root / relative
    # Same check as ``candidate.resolve().relative_to(root.resolve())`` (realpath on both
    # sides, then segment-wise containment) without pathlib's per-parent object churn,
    # which dominated listings of tens of thousands of day partitions.
    resolved_root = os.path.realpath(root)
    resolved = os.path.realpath(candidate)
    if os.path.commonpath((resolved, resolved_root)) != resolved_root:
        message = "Dataset verification failed because a manifest file path escapes its root."
        raise DatasetStoreError(message)
    return candidate


def _report_facts_match(
    supplied: CandleRangeReport,
    recomputed: CandleRangeReport,
) -> bool:
    """Compare every range fact persisted in a dataset manifest."""
    return (
        supplied.starts_at == recomputed.starts_at
        and supplied.ends_at == recomputed.ends_at
        and supplied.requested_candle_count == recomputed.requested_candle_count
        and supplied.quality.candles == recomputed.quality.candles
        and supplied.quality.candle_count == recomputed.quality.candle_count
        and supplied.quality.gap_count == recomputed.quality.gap_count
        and supplied.quality.missing_intervals == recomputed.quality.missing_intervals
        and supplied.quality.latest_completed_at == recomputed.quality.latest_completed_at
        and supplied.complete == recomputed.complete
    )
