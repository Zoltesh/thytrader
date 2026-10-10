"""Dataset manifest facts, the dataset error, and the manifest schema and payload codec.

:class:`DatasetManifest` identifies one immutable persisted candle range and its files;
:class:`DatasetStoreError` is raised by every dataset check. This module also owns the
supported manifest schema versions, the JSON payload a manifest is published as, and the
optional no-trade bar count (ADR 0095) and the futures volume unit (ADR 0126). Imports no
other dataset module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id

if TYPE_CHECKING:
    from pathlib import Path


_DATASET_SCHEMA_VERSION = 2
_SUPPORTED_DATASET_SCHEMA_VERSIONS = frozenset({1, 2})


class DatasetStoreError(ValueError):
    """Raised when a candle range or on-disk dataset is not safe to use."""


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Facts identifying one immutable persisted candle range and its files.

    ``synthetic_no_trade_intervals`` counts flat zero-volume bars the worker published for
    confirmed no-trade intervals (ADR 0095). The manifest stores it only when it is
    non-zero, so gap-free datasets keep their exact bytes, and it is not part of the
    content fingerprint: deep verification recomputes it from the rows.
    """

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
    synthetic_no_trade_intervals: int = 0


def _require_supported_schema_version(value: object) -> int:
    """Reject manifests whose schema version is outside the supported immutable set."""
    if not isinstance(value, int) or value not in _SUPPORTED_DATASET_SCHEMA_VERSIONS:
        message = "Dataset verification failed because the manifest schema is unsupported."
        raise DatasetStoreError(message)
    return value


def _manifest_payload(manifest: DatasetManifest) -> dict[str, object]:
    """Serialize a manifest without host-specific absolute paths."""
    payload: dict[str, object] = {
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
    if manifest.synthetic_no_trade_intervals:
        # Written only when non-zero, so gap-free manifests keep their exact bytes.
        payload["synthetic_no_trade_intervals"] = manifest.synthetic_no_trade_intervals
    if is_futures_product_id(manifest.product_id):
        # Futures volume is in contracts; written only for futures, so spot bytes never move.
        payload[_VOLUME_UNIT_KEY] = FUTURES_VOLUME_UNIT
    return payload


_VOLUME_UNIT_KEY = "volume_unit"
FUTURES_VOLUME_UNIT = "contracts"


def _require_volume_unit(payload: dict[str, object], product_id: str) -> None:
    """A futures manifest must say its volume is in contracts; a spot manifest says nothing.

    The content fingerprint is unchanged: the product id already binds the unit.
    """
    expected = FUTURES_VOLUME_UNIT if is_futures_product_id(product_id) else None
    if payload.get(_VOLUME_UNIT_KEY) != expected or (
        expected is None and _VOLUME_UNIT_KEY in payload
    ):
        message = "Dataset verification failed because the manifest volume unit is inconsistent."
        raise DatasetStoreError(message)


_NO_TRADE_COUNT_KEY = "synthetic_no_trade_intervals"


def _manifest_no_trade_count(payload: dict[str, object], received_candle_count: int) -> int:
    """Read the optional no-trade bar count; an absent key means zero (pre-ADR 0095)."""
    value = payload.get(_NO_TRADE_COUNT_KEY, 0)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > received_candle_count
    ):
        message = "Dataset verification failed because manifest facts are malformed."
        raise DatasetStoreError(message)
    return value


def _with_verified_no_trade_count(
    manifest: DatasetManifest, payload: dict[str, object], verified_count: int
) -> DatasetManifest:
    """Bind the no-trade count recomputed from rows; a stored count must agree with it.

    Manifests written before ADR 0095 carry no count; theirs is taken from the rows.
    """
    if _NO_TRADE_COUNT_KEY in payload and manifest.synthetic_no_trade_intervals != verified_count:
        message = "Dataset verification failed because manifest facts do not match candle coverage."
        raise DatasetStoreError(message)
    return replace(manifest, synthetic_no_trade_intervals=verified_count)
