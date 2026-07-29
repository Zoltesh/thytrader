"""Immutable Parquet storage for validated historical candle ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import TYPE_CHECKING

import polars as pl

from thytrader.market_data.models import CandleInterval, CandleRangeReport

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


_DATASET_SCHEMA_VERSION = 1


class DatasetStoreError(ValueError):
    """Raised when a candle range is unsafe to persist as an immutable dataset."""


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
    """Write complete validated ranges as immutable date-partitioned Parquet datasets."""

    def __init__(self, root: Path) -> None:
        """Configure the local root under which immutable datasets are created."""
        self._root = root

    def write(
        self,
        provider: str,
        product_id: str,
        report: CandleRangeReport,
    ) -> DatasetManifest:
        """Persist a complete range or reject it before any dataset files are created."""
        if not report.complete:
            message = "Only a complete historical range can be persisted as a dataset."
            raise DatasetStoreError(message)
        if not report.quality.candles:
            message = "A complete dataset must contain at least one candle."
            raise DatasetStoreError(message)

        rows = _candle_rows(report)
        digest = _fingerprint(rows)
        timeframe = CandleInterval.ONE_HOUR.value
        files = tuple(
            self._write_partition(provider, product_id, timeframe, day, day_rows, digest)
            for day, day_rows in _partition_rows(rows).items()
        )
        manifest_path = self._write_manifest(
            provider,
            product_id,
            timeframe,
            report,
            digest,
            files,
        )
        return DatasetManifest(
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

    def _write_partition(
        self,
        provider: str,
        product_id: str,
        timeframe: str,
        day: tuple[int, int, int],
        rows: Sequence[dict[str, str]],
        digest: str,
    ) -> Path:
        """Atomically write one immutable calendar-day partition without replacing existing data."""
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
            return path
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".parquet.tmp")
        pl.DataFrame(rows).write_parquet(temporary)
        temporary.replace(path)
        return path

    def _write_manifest(
        self,
        provider: str,
        product_id: str,
        timeframe: str,
        report: CandleRangeReport,
        digest: str,
        files: tuple[Path, ...],
    ) -> Path:
        """Atomically write deterministic metadata adjacent to the dataset root."""
        directory = self._root / "manifests"
        path = directory / f"{digest}.json"
        if path.exists():
            return path
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
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
            "content_fingerprint": f"sha256:{digest}",
            "files": [str(file.relative_to(self._root)) for file in files],
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        temporary.replace(path)
        return path


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


def _fingerprint(rows: Sequence[dict[str, str]]) -> str:
    """Hash canonical serialized candle content for immutable dataset identity."""
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _utc_text(value: datetime) -> str:
    """Serialize an aware timestamp in canonical UTC RFC3339 form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
