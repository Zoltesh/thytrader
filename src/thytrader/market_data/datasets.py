"""Immutable Parquet storage for validated historical candle ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
import re
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import polars as pl

from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import (
    CandleQualityError,
    analyze_range,
    validate_candle_values,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


_DATASET_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_FINGERPRINT = re.compile(r"^sha256:([0-9a-f]{64})$")


class DatasetStoreError(ValueError):
    """Raised when a candle range or on-disk dataset is not safe to use."""


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Facts identifying one immutable persisted candle range and its files."""

    provider: str
    product_id: str
    timeframe: str
    starts_at: str
    ends_at: str
    expected_candle_count: int
    received_candle_count: int
    gap_count: int
    missing_intervals: int
    complete: bool
    content_fingerprint: str
    files: tuple[Path, ...]
    manifest_path: Path


class DatasetStore:
    """Write and verify complete validated ranges as immutable date-partitioned datasets."""

    def __init__(self, root: Path) -> None:
        """Configure the local root under which immutable datasets are published."""
        self._root = root

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

        timeframe = CandleInterval.ONE_HOUR.value
        rows = _candle_rows(report)
        digest = _fingerprint(provider, product_id, timeframe, report, rows)
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

    def list_verified(self) -> tuple[DatasetManifest, ...]:
        """Return every complete immutable dataset whose manifest re-verifies from disk."""
        manifests = self._root / "manifests"
        if not manifests.exists():
            return ()
        verified: list[DatasetManifest] = []
        for path in sorted(manifests.glob("*.json"), reverse=True):
            try:
                verified.append(self.load_verified(path))
            except DatasetStoreError:
                continue
        return tuple(verified)

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Resolve and verify exact typed candles by immutable dataset fingerprint."""
        manifest = self.load_manifest(content_fingerprint)
        rows = tuple(row for file in manifest.files for row in _parquet_rows(file))
        return _rows_to_candles(rows)

    def load_manifest(self, content_fingerprint: str) -> DatasetManifest:
        """Resolve and verify exact dataset identity and coverage by content fingerprint."""
        return self.load_verified(self._manifest_path(content_fingerprint))

    def extend(self, content_fingerprint: str, report: CandleRangeReport) -> DatasetManifest:
        """Publish a cumulative revision by merging a verified dataset with one overlap range."""
        _validate_report_for_publication(report)
        prior = self.load_verified(self._manifest_path(content_fingerprint))
        prior_file_rows = {file: _parquet_rows(file) for file in prior.files}
        prior_rows = tuple(row for rows in prior_file_rows.values() for row in rows)
        prior_candles = _rows_to_candles(prior_rows)
        prior_start = _parse_utc_text(prior.starts_at)
        prior_end = _parse_utc_text(prior.ends_at)
        if report.starts_at >= prior_end or report.ends_at <= prior_end:
            message = "Dataset extension must overlap and advance the prior verified range."
            raise DatasetStoreError(message)
        merged = {candle.starts_at: candle for candle in prior_candles}
        merged.update({candle.starts_at: candle for candle in report.quality.candles})
        combined = analyze_range(
            tuple(merged.values()),
            CandleInterval.ONE_HOUR,
            prior_start,
            report.ends_at,
            report.ends_at,
        )
        if not combined.complete:
            message = "Dataset extension did not produce complete contiguous coverage."
            raise DatasetStoreError(message)

        rows = _candle_rows(combined)
        digest = _fingerprint(prior.provider, prior.product_id, prior.timeframe, combined, rows)
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

    def _manifest_path(self, content_fingerprint: str) -> Path:
        """Resolve a validated fingerprint to its canonical manifest path."""
        match = _FINGERPRINT.fullmatch(content_fingerprint)
        if match is None:
            message = "Dataset lookup requires a valid content fingerprint."
            raise DatasetStoreError(message)
        return self._root / "manifests" / f"{match.group(1)}.json"

    def load_verified(self, manifest_path: Path) -> DatasetManifest:
        """Load one manifest and reject missing, malformed, or content-mismatched dataset files."""
        try:
            payload = json.loads(manifest_path.read_text())
            manifest = self._manifest_from_payload(payload, manifest_path)
            rows = tuple(row for file in manifest.files for row in _parquet_rows(file))
            range_report = analyze_range(
                _rows_to_candles(rows),
                CandleInterval.ONE_HOUR,
                _parse_utc_text(manifest.starts_at),
                _parse_utc_text(manifest.ends_at),
                _parse_utc_text(manifest.ends_at) + CandleInterval.ONE_HOUR.duration,
            )
        except DatasetStoreError:
            raise
        except (
            CandleQualityError,
            InvalidOperation,
            OSError,
            OverflowError,
            ValueError,
            json.JSONDecodeError,
            pl.exceptions.PolarsError,
        ) as error:
            message = "Dataset verification failed while reading its manifest or Parquet files."
            raise DatasetStoreError(message) from error

        if (
            not range_report.complete
            or range_report.requested_candle_count != manifest.expected_candle_count
            or range_report.quality.candle_count != manifest.received_candle_count
            or range_report.quality.gap_count != manifest.gap_count
            or range_report.quality.missing_intervals != manifest.missing_intervals
        ):
            message = (
                "Dataset verification failed because manifest facts do not match candle coverage."
            )
            raise DatasetStoreError(message)
        expected = _fingerprint_from_manifest(manifest, rows)
        if manifest.content_fingerprint != f"sha256:{expected}":
            message = "Dataset verification failed because its content fingerprint does not match."
            raise DatasetStoreError(message)
        return manifest

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

    def _manifest_from_payload(self, payload: object, manifest_path: Path) -> DatasetManifest:
        """Validate untrusted manifest JSON before using any referenced dataset file."""
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _DATASET_SCHEMA_VERSION
        ):
            message = "Dataset verification failed because the manifest schema is unsupported."
            raise DatasetStoreError(message)
        manifest_payload = cast("dict[str, object]", payload)
        required_text = (
            "provider",
            "product_id",
            "timeframe",
            "starts_at",
            "ends_at",
            "content_fingerprint",
        )
        if any(not isinstance(manifest_payload.get(key), str) for key in required_text):
            message = "Dataset verification failed because the manifest is malformed."
            raise DatasetStoreError(message)
        provider = cast("str", manifest_payload["provider"])
        product_id = cast("str", manifest_payload["product_id"])
        timeframe = cast("str", manifest_payload["timeframe"])
        content_fingerprint = cast("str", manifest_payload["content_fingerprint"])
        _validate_identifier(provider)
        _validate_identifier(product_id)
        if timeframe != CandleInterval.ONE_HOUR.value:
            message = "Dataset verification failed because the timeframe is unsupported."
            raise DatasetStoreError(message)
        fingerprint_match = _FINGERPRINT.fullmatch(content_fingerprint)
        if fingerprint_match is None:
            message = "Dataset verification failed because the content fingerprint is malformed."
            raise DatasetStoreError(message)
        expected_manifest_path = self._root / "manifests" / f"{fingerprint_match.group(1)}.json"
        if manifest_path.resolve() != expected_manifest_path.resolve():
            message = (
                "Dataset verification failed: manifest is not at its canonical publication path."
            )
            raise DatasetStoreError(message)
        files_value = manifest_payload.get("files")
        if (
            not files_value
            or not isinstance(files_value, list)
            or not all(isinstance(item, str) for item in files_value)
        ):
            message = "Dataset verification failed because manifest files are malformed."
            raise DatasetStoreError(message)
        files = tuple(
            _safe_dataset_path(self._root, item) for item in cast("list[str]", files_value)
        )
        numeric = (
            "expected_candle_count",
            "received_candle_count",
            "gap_count",
            "missing_intervals",
        )
        if any(
            not isinstance(manifest_payload.get(key), int)
            or isinstance(manifest_payload.get(key), bool)
            for key in numeric
        ) or not isinstance(manifest_payload.get("complete"), bool):
            message = "Dataset verification failed because manifest facts are malformed."
            raise DatasetStoreError(message)
        complete = cast("bool", manifest_payload["complete"])
        if not complete:
            message = "Dataset verification failed because only complete datasets may be loaded."
            raise DatasetStoreError(message)
        starts_at = cast("str", manifest_payload["starts_at"])
        ends_at = cast("str", manifest_payload["ends_at"])
        duration = _parse_utc_text(ends_at) - _parse_utc_text(starts_at)
        expected_candle_count = cast("int", manifest_payload["expected_candle_count"])
        received_candle_count = cast("int", manifest_payload["received_candle_count"])
        gap_count = cast("int", manifest_payload["gap_count"])
        missing_intervals = cast("int", manifest_payload["missing_intervals"])
        if (
            duration <= timedelta(0)
            or duration % CandleInterval.ONE_HOUR.duration != timedelta(0)
            or expected_candle_count != duration // CandleInterval.ONE_HOUR.duration
            or received_candle_count != expected_candle_count
            or gap_count != 0
            or missing_intervals != 0
        ):
            message = "Dataset verification failed because manifest facts are inconsistent."
            raise DatasetStoreError(message)
        return DatasetManifest(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            starts_at=starts_at,
            ends_at=ends_at,
            expected_candle_count=expected_candle_count,
            received_candle_count=received_candle_count,
            gap_count=gap_count,
            missing_intervals=missing_intervals,
            complete=complete,
            content_fingerprint=content_fingerprint,
            files=files,
            manifest_path=manifest_path,
        )


def _validate_identifier(value: str) -> None:
    """Reject filesystem-unsafe provider and product identifier values."""
    if not _IDENTIFIER.fullmatch(value):
        message = "Dataset identifier contains unsafe filesystem characters."
        raise DatasetStoreError(message)


def _validate_report_for_publication(report: CandleRangeReport) -> None:
    """Recompute every durable range fact before a report can publish dataset files."""
    if not report.complete:
        message = "Only a complete historical range can be persisted as a dataset."
        raise DatasetStoreError(message)
    if not report.quality.candles:
        message = "A complete dataset must contain at least one candle."
        raise DatasetStoreError(message)
    try:
        recomputed_report = analyze_range(
            tuple(report.quality.candles),
            CandleInterval.ONE_HOUR,
            report.starts_at,
            report.ends_at,
            report.ends_at + CandleInterval.ONE_HOUR.duration,
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
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as error:
        message = "Dataset verification failed because a manifest file path escapes its root."
        raise DatasetStoreError(message) from error
    return candidate


def _fsync_file(path: Path) -> None:
    """Flush a completed temporary data file before its durable publication rename."""
    with path.open("rb") as file:
        os.fsync(file.fileno())


def _fsync_directory(path: Path) -> None:
    """Flush a directory entry after atomically publishing a data or manifest file."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


def _candle_rows(report: CandleRangeReport) -> tuple[dict[str, str], ...]:
    """Serialize exact candles into canonical rows suitable for hashing and Parquet storage."""
    return tuple(
        {
            "starts_at": _utc_text(candle.starts_at),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
        }
        for candle in report.quality.candles
    )


def _parquet_rows(path: Path) -> tuple[dict[str, str], ...]:
    """Read canonical string rows from one persisted Parquet file for fingerprint verification."""
    frame = pl.read_parquet(path)
    expected_columns = ["starts_at", "open", "high", "low", "close", "volume"]
    if frame.columns != expected_columns:
        message = "Dataset verification failed because Parquet columns differ from the schema."
        raise DatasetStoreError(message)
    rows = frame.to_dicts()
    if any(not all(isinstance(value, str) for value in row.values()) for row in rows):
        message = "Dataset verification failed because Parquet values differ from the schema."
        raise DatasetStoreError(message)
    return tuple(rows)


def _rows_to_candles(rows: Sequence[dict[str, str]]) -> tuple[Candle, ...]:
    """Reconstruct exact domain candles from verified canonical Parquet row values."""
    try:
        candles = tuple(
            Candle(
                starts_at=_parse_utc_text(row["starts_at"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            for row in rows
        )
        for candle in candles:
            validate_candle_values(candle)
    except (InvalidOperation, KeyError, ValueError) as error:
        message = "Dataset verification failed because Parquet rows are not valid candle values."
        raise DatasetStoreError(message) from error
    else:
        return candles


def _parse_utc_text(value: str) -> datetime:
    """Parse only canonical UTC RFC3339 timestamps used by manifests and Parquet rows."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        message = "Dataset verification failed because a timestamp is malformed."
        raise DatasetStoreError(message) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or _utc_text(parsed) != value:
        message = "Dataset verification failed because a timestamp is not canonical UTC."
        raise DatasetStoreError(message)
    return parsed


def _partition_rows(
    rows: Sequence[dict[str, str]],
) -> dict[tuple[int, int, int], list[dict[str, str]]]:
    """Group canonical UTC candle rows into calendar-day Parquet partitions."""
    partitions: dict[tuple[int, int, int], list[dict[str, str]]] = {}
    for row in rows:
        starts_at = datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00"))
        key = (starts_at.year, starts_at.month, starts_at.day)
        partitions.setdefault(key, []).append(row)
    return partitions


def _fingerprint(
    provider: str,
    product_id: str,
    timeframe: str,
    report: CandleRangeReport,
    rows: Sequence[dict[str, str]],
) -> str:
    """Hash complete identity and canonical candle content for immutable dataset identity."""
    identity = {
        "schema_version": _DATASET_SCHEMA_VERSION,
        "provider": provider,
        "product_id": product_id,
        "timeframe": timeframe,
        "starts_at": _utc_text(report.starts_at),
        "ends_at": _utc_text(report.ends_at),
        "expected_candle_count": report.requested_candle_count,
        "received_candle_count": report.quality.candle_count,
        "gap_count": report.quality.gap_count,
        "missing_intervals": report.quality.missing_intervals,
        "complete": report.complete,
        "rows": rows,
    }
    return sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fingerprint_from_manifest(manifest: DatasetManifest, rows: Sequence[dict[str, str]]) -> str:
    """Reconstruct the immutable fingerprint using persisted manifest facts and Parquet content."""
    identity = {
        "schema_version": _DATASET_SCHEMA_VERSION,
        "provider": manifest.provider,
        "product_id": manifest.product_id,
        "timeframe": manifest.timeframe,
        "starts_at": manifest.starts_at,
        "ends_at": manifest.ends_at,
        "expected_candle_count": manifest.expected_candle_count,
        "received_candle_count": manifest.received_candle_count,
        "gap_count": manifest.gap_count,
        "missing_intervals": manifest.missing_intervals,
        "complete": manifest.complete,
        "rows": rows,
    }
    return sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _manifest_payload(manifest: DatasetManifest) -> dict[str, object]:
    """Serialize a manifest without host-specific absolute paths."""
    return {
        "schema_version": _DATASET_SCHEMA_VERSION,
        "provider": manifest.provider,
        "product_id": manifest.product_id,
        "timeframe": manifest.timeframe,
        "starts_at": manifest.starts_at,
        "ends_at": manifest.ends_at,
        "expected_candle_count": manifest.expected_candle_count,
        "received_candle_count": manifest.received_candle_count,
        "gap_count": manifest.gap_count,
        "missing_intervals": manifest.missing_intervals,
        "complete": manifest.complete,
        "content_fingerprint": manifest.content_fingerprint,
        "files": [
            str(file.relative_to(manifest.manifest_path.parent.parent)) for file in manifest.files
        ],
    }


def _utc_text(value: datetime) -> str:
    """Serialize an aware timestamp in canonical UTC RFC3339 form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
