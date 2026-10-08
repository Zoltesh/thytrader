"""Canonical candle row encoding, Parquet row decoding, and content fingerprints.

Rows keep each ``Decimal``'s source spelling in Parquet while schema-v2 fingerprints hash
canonical decimals; the fingerprint is the immutable, content-addressed dataset identity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import TYPE_CHECKING

import polars as pl

from thytrader.decimal_text import canonical_decimal
from thytrader.market_data.dataset_manifest import DatasetStoreError
from thytrader.market_data.models import Candle
from thytrader.market_data.quality import validate_candle_values

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from thytrader.market_data.dataset_manifest import DatasetManifest
    from thytrader.market_data.models import CandleRangeReport


_FINGERPRINT_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


def _candle_rows(report: CandleRangeReport) -> tuple[dict[str, str], ...]:
    """Serialize exact candles into Parquet rows using each ``Decimal``'s source spelling.

    Immutable dataset identity for schema v2 uses ``_fingerprint_rows`` instead, which
    normalizes OHLCV through ``canonical_decimal`` while Parquet keeps these literals.
    """
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


def _fingerprint_rows(report: CandleRangeReport) -> tuple[dict[str, str], ...]:
    """Serialize OHLCV with canonical decimals for schema-v2 content fingerprints."""
    return tuple(
        {
            "starts_at": _utc_text(candle.starts_at),
            "open": canonical_decimal(candle.open),
            "high": canonical_decimal(candle.high),
            "low": canonical_decimal(candle.low),
            "close": canonical_decimal(candle.close),
            "volume": canonical_decimal(candle.volume),
        }
        for candle in report.quality.candles
    )


def _rows_for_fingerprint(
    rows: Sequence[dict[str, str]],
    schema_version: int,
) -> tuple[dict[str, str], ...]:
    """Normalize persisted Parquet rows to the schema-specific fingerprint spelling."""
    if schema_version == 1:
        return tuple(rows)
    return tuple(
        {
            **row,
            **{
                field: canonical_decimal(Decimal(row[field])) for field in _FINGERPRINT_OHLCV_FIELDS
            },
        }
        for row in rows
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
    *,
    schema_version: int,
) -> str:
    """Hash complete identity and canonical candle content for immutable dataset identity."""
    identity = {
        "schema_version": schema_version,
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


def _fingerprint_from_manifest(
    manifest: DatasetManifest,
    rows: Sequence[dict[str, str]],
    *,
    schema_version: int,
) -> str:
    """Reconstruct the immutable fingerprint using persisted manifest facts and Parquet content."""
    identity = {
        "schema_version": schema_version,
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


def _utc_text(value: datetime) -> str:
    """Serialize an aware timestamp in canonical UTC RFC3339 form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
