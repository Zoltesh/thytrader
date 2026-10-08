"""Immutable Parquet storage for validated historical candle ranges.

:class:`DatasetStore` writes and extends datasets and publishes their manifests. It
inherits catalog listings from :mod:`thytrader.market_data.dataset_catalog` and verified
reads and caches from :mod:`thytrader.market_data.dataset_verification`. The manifest
model, error, and manifest codec live in :mod:`thytrader.market_data.dataset_manifest`;
file identity and durability helpers in :mod:`thytrader.market_data.dataset_files`; input
validation in :mod:`thytrader.market_data.dataset_validation`; row encoding and content
fingerprints in :mod:`thytrader.market_data.dataset_content`. Names other modules import
or patch from here are re-exported (``__all__``).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl

from thytrader.market_data.dataset_catalog import _DatasetCatalog
from thytrader.market_data.dataset_content import (
    _candle_rows,
    _fingerprint,
    _fingerprint_rows,
    _parquet_rows,
    _parse_utc_text,
    _partition_rows,
    _rows_to_candles,
    _utc_text,
)
from thytrader.market_data.dataset_files import (
    _file_identity,
    _fsync_directory,
    _fsync_file,
)
from thytrader.market_data.dataset_manifest import (
    _DATASET_SCHEMA_VERSION,
    DatasetManifest,
    DatasetStoreError,
    _manifest_payload,
)
from thytrader.market_data.dataset_validation import (
    _require_timeframe,
    _validate_identifier,
    _validate_report_for_publication,
)
from thytrader.market_data.dataset_verification import DEFAULT_VERIFIED_CANDLE_CACHE_BUDGET
from thytrader.market_data.models import CandleRangeReport, interval_from_range
from thytrader.market_data.no_trade import count_no_trade_bars
from thytrader.market_data.quality import analyze_range

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "DEFAULT_VERIFIED_CANDLE_CACHE_BUDGET",
    "DatasetManifest",
    "DatasetStore",
    "DatasetStoreError",
    "_file_identity",
    "_fingerprint",
    "_parquet_rows",
]


class DatasetStore(_DatasetCatalog):
    """Write and verify complete validated ranges as immutable date-partitioned datasets."""

    def write(
        self,
        provider: str,
        product_id: str,
        report: CandleRangeReport,
    ) -> DatasetManifest:
        """Write a complete range and publish its manifest only after all files are present."""
        _validate_identifier(provider)
        _validate_identifier(product_id)
        _validate_report_for_publication(report)

        timeframe = interval_from_range(report).value
        rows = _candle_rows(report)
        digest = _fingerprint(
            provider,
            product_id,
            timeframe,
            report,
            _fingerprint_rows(report),
            schema_version=_DATASET_SCHEMA_VERSION,
        )
        manifest_path = self._root / "manifests" / f"{digest}.json"
        if manifest_path.exists():
            return self.load_verified(manifest_path)

        files = tuple(
            self._write_partition(provider, product_id, timeframe, day, day_rows, digest)
            for day, day_rows in _partition_rows(rows).items()
        )
        return self._publish_manifest(
            provider,
            product_id,
            timeframe,
            report,
            digest,
            files,
            manifest_path,
        )

    def extend(self, content_fingerprint: str, report: CandleRangeReport) -> DatasetManifest:
        """Publish a cumulative revision by merging a verified overlap that expands start or end."""
        _validate_report_for_publication(report)
        prior = self.load_verified(self._manifest_path(content_fingerprint))
        prior_file_rows = {file: _parquet_rows(file) for file in prior.files}
        prior_rows = tuple(row for rows in prior_file_rows.values() for row in rows)
        prior_candles = _rows_to_candles(prior_rows)
        prior_start = _parse_utc_text(prior.starts_at)
        prior_end = _parse_utc_text(prior.ends_at)
        interval = _require_timeframe(prior.timeframe)
        if interval_from_range(report) is not interval:
            message = "Dataset extension timeframe must match the prior dataset."
            raise DatasetStoreError(message)
        overlaps = report.starts_at < prior_end and report.ends_at > prior_start
        expands = report.starts_at < prior_start or report.ends_at > prior_end
        if not overlaps or not expands:
            message = (
                "Dataset extension must overlap the prior verified range and expand "
                "its start or end."
            )
            raise DatasetStoreError(message)
        merged = {candle.starts_at: candle for candle in prior_candles}
        merged.update({candle.starts_at: candle for candle in report.quality.candles})
        combined_start = min(prior_start, report.starts_at)
        combined_end = max(prior_end, report.ends_at)
        combined = analyze_range(
            tuple(merged.values()),
            interval,
            combined_start,
            combined_end,
            combined_end,
        )
        if not combined.complete:
            message = "Dataset extension did not produce complete contiguous coverage."
            raise DatasetStoreError(message)

        rows = _candle_rows(combined)
        digest = _fingerprint(
            prior.provider,
            prior.product_id,
            prior.timeframe,
            combined,
            _fingerprint_rows(combined),
            schema_version=_DATASET_SCHEMA_VERSION,
        )
        manifest_path = self._root / "manifests" / f"{digest}.json"
        if manifest_path.exists():
            return self.load_verified(manifest_path)

        prior_by_day = {
            next(iter(_partition_rows(file_rows))): file
            for file, file_rows in prior_file_rows.items()
            if file_rows
        }
        affected_days = set(_partition_rows(_candle_rows(report)))
        files = tuple(
            prior_by_day[day]
            if day not in affected_days and day in prior_by_day
            else self._write_partition(
                prior.provider,
                prior.product_id,
                prior.timeframe,
                day,
                day_rows,
                digest,
            )
            for day, day_rows in _partition_rows(rows).items()
        )
        return self._publish_manifest(
            prior.provider,
            prior.product_id,
            prior.timeframe,
            combined,
            digest,
            files,
            manifest_path,
        )

    def _write_partition(
        self,
        provider: str,
        product_id: str,
        timeframe: str,
        day: tuple[int, int, int],
        rows: Sequence[dict[str, str]],
        digest: str,
    ) -> Path:
        """Atomically create one immutable calendar-day file without replacing existing data."""
        year, month, date = day
        directory = (
            self._root
            / provider
            / product_id
            / timeframe
            / str(year)
            / f"{month:02}"
            / f"{date:02}"
        )
        path = directory / f"part-{digest}.parquet"
        if path.exists():
            message = "Dataset file already exists without a verified published manifest."
            raise DatasetStoreError(message)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{path.name}.{uuid4().hex}.tmp"
        try:
            pl.DataFrame(rows).write_parquet(temporary)
            _fsync_file(temporary)
            temporary.replace(path)
            _fsync_directory(directory)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return path

    def _publish_manifest(
        self,
        provider: str,
        product_id: str,
        timeframe: str,
        report: CandleRangeReport,
        digest: str,
        files: tuple[Path, ...],
        manifest_path: Path,
    ) -> DatasetManifest:
        """Atomically publish the manifest that makes fully written dataset files discoverable."""
        directory = manifest_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        manifest = DatasetManifest(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            starts_at=_utc_text(report.starts_at),
            ends_at=_utc_text(report.ends_at),
            expected_candle_count=report.requested_candle_count,
            received_candle_count=report.quality.candle_count,
            gap_count=report.quality.gap_count,
            missing_intervals=report.quality.missing_intervals,
            complete=report.complete,
            content_fingerprint=f"sha256:{digest}",
            files=files,
            manifest_path=manifest_path,
            synthetic_no_trade_intervals=count_no_trade_bars(report.quality.candles),
        )
        payload = _manifest_payload(manifest)
        temporary = directory / f".{manifest_path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as file:
                file.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(manifest_path)
            _fsync_directory(directory)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        # A manifest is the sole publication marker; files created before it remain undiscoverable.
        return manifest
