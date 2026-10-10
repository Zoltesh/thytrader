"""Verified dataset reads and the dataset store's verification caches.

:class:`_DatasetVerifier` holds the store's root and caches, resolves a content
fingerprint to its canonical manifest, fully verifies a dataset (manifest facts, candle
coverage and content fingerprint), and serves a cached verification only while every
file keeps the exact stat identity and SHA-256 digest that verification read.
:class:`thytrader.market_data.datasets.DatasetStore` builds on it.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import InvalidOperation
import json
import re
from threading import Lock, RLock
from typing import TYPE_CHECKING, cast

import polars as pl

from thytrader.market_data.dataset_content import (
    _fingerprint_from_manifest,
    _parquet_rows,
    _parse_utc_text,
    _rows_for_fingerprint,
    _rows_to_candles,
)
from thytrader.market_data.dataset_files import (
    _dataset_identity,
    _file_identity,
    _FileIdentity,
    _files_identity,
    _ListingStamp,
    _StatIdentity,
)
from thytrader.market_data.dataset_manifest import (
    DatasetManifest,
    DatasetStoreError,
    _manifest_no_trade_count,
    _require_supported_schema_version,
    _require_volume_unit,
    _with_verified_no_trade_count,
)
from thytrader.market_data.dataset_validation import (
    _require_timeframe,
    _safe_dataset_path,
    _validate_identifier,
)
from thytrader.market_data.no_trade import count_no_trade_bars
from thytrader.market_data.quality import CandleQualityError, analyze_range

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from thytrader.market_data.models import Candle


# Verified datasets kept per process. Each hit is re-checked against the stat identity and
# SHA-256 digest of the manifest and every Parquet file, so a cached entry never serves
# bytes that differ from the bytes that were verified.
_VERIFIED_DATASET_CACHE_ENTRIES = 256
DEFAULT_VERIFIED_CANDLE_CACHE_BUDGET = 120_000
_FINGERPRINT = re.compile(r"^sha256:([0-9a-f]{64})$")


@dataclass(frozen=True, slots=True)
class _VerifiedDataset:
    """One fully verified dataset plus the exact file identities its verification read.

    ``identity`` is None when the files changed while they were being verified; such a
    result is returned once but never cached. ``candles`` is None when the dataset is
    larger than the store's candle cache budget.
    """

    manifest: DatasetManifest
    candles: tuple[Candle, ...] | None
    identity: tuple[_FileIdentity, ...] | None


@dataclass(frozen=True, slots=True)
class _DatasetCatalogCandidate:
    """Cheaply parsed manifest identity used to rank deep-verification candidates."""

    provider: str
    product_id: str
    timeframe: str
    starts_at: datetime
    ends_at: datetime
    manifest_path: Path


class _DatasetVerifier:
    """Resolve, verify, and cache immutable datasets under one dataset root."""

    def __init__(
        self,
        root: Path,
        *,
        candle_cache_budget: int = DEFAULT_VERIFIED_CANDLE_CACHE_BUDGET,
    ) -> None:
        """Configure the dataset root and how many verified candles to keep in memory.

        ``candle_cache_budget`` bounds decoded candles held for ``load_candles``; zero keeps
        only verified manifests (the ingest worker never needs candles back).
        """
        self._root = root
        self._catalog_lock = RLock()
        self._verified_cache: dict[Path, tuple[tuple[_FileIdentity, ...], DatasetManifest]] = {}
        # Ranking metadata per manifest path, keyed by (st_size, st_mtime_ns). Manifests are
        # content-addressed and never rewritten in place, so a stat match means the parsed
        # metadata is still exact; deep verification still runs through _verified_cache.
        self._candidate_cache: dict[Path, tuple[tuple[int, int], _DatasetCatalogCandidate]] = {}
        # Catalog-grade latest-listing entries, valid while the manifest and every file keep
        # the stat identity captured when the entry passed its structural checks.
        self._listing_cache: dict[Path, tuple[_ListingStamp, DatasetManifest]] = {}
        # Parquet files whose envelope (magic at both ends) passed under an exact identity.
        self._envelope_cache: dict[Path, _StatIdentity] = {}
        # Manifest-relative partition paths already validated for catalog listings.
        self._catalog_paths: dict[str, Path] = {}
        self._content_lock = Lock()
        self._content_cache: OrderedDict[Path, _VerifiedDataset] = OrderedDict()
        self._candle_cache_budget = max(0, candle_cache_budget)
        self._cached_candle_count = 0

    def _identity_matches(self, identity: tuple[_FileIdentity, ...]) -> bool:
        """Check that every cached file still has its verified filesystem and content identity."""
        return all(_file_identity(expected[0]) == expected for expected in identity)

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Resolve and verify exact typed candles by immutable dataset fingerprint.

        The candles are the ones decoded while verifying the content fingerprint (or a cached
        copy whose files still carry their verified SHA-256 digests), never a second,
        unverified read of the Parquet files.
        """
        dataset = self._verified_dataset(
            self._manifest_path(content_fingerprint), need_candles=True
        )
        if dataset.candles is None:
            message = "Dataset verification did not yield candles."
            raise DatasetStoreError(message)
        return dataset.candles

    def load_manifest(self, content_fingerprint: str) -> DatasetManifest:
        """Resolve and verify exact dataset identity and coverage by content fingerprint."""
        return self.load_verified(self._manifest_path(content_fingerprint))

    def load_edge_candle(self, content_fingerprint: str, *, newest: bool) -> Candle:
        """Return the first or the last bar of one verified dataset.

        The dataset is verified first (or served from a byte-identical cached
        verification); then only its first or last UTC-day partition is decoded. The
        ingest worker uses this bar to extend a series whose edge is a stored no-trade
        bar; ``extend`` verifies the whole prior dataset again before it publishes.
        """
        manifest = self.load_manifest(content_fingerprint)
        path = manifest.files[-1] if newest else manifest.files[0]
        try:
            candles = _rows_to_candles(_parquet_rows(path))
        except (OSError, ValueError, pl.exceptions.PolarsError) as error:
            message = "Dataset verification failed while reading an edge partition."
            raise DatasetStoreError(message) from error
        if not candles:
            message = "Dataset verification failed because an edge partition is empty."
            raise DatasetStoreError(message)
        return candles[-1] if newest else candles[0]

    def _manifest_path(self, content_fingerprint: str) -> Path:
        """Resolve a validated fingerprint to its canonical manifest path."""
        match = _FINGERPRINT.fullmatch(content_fingerprint)
        if match is None:
            message = "Dataset lookup requires a valid content fingerprint."
            raise DatasetStoreError(message)
        return self._root / "manifests" / f"{match.group(1)}.json"

    def load_verified(self, manifest_path: Path) -> DatasetManifest:
        """Load one manifest and reject missing, malformed, or content-mismatched dataset files.

        A dataset verified earlier in this process is served from cache only while its
        manifest and every Parquet file keep the exact stat identity and SHA-256 digest
        captured around that verification. Any difference re-runs full verification, so a
        cache hit never vouches for bytes other than the bytes that were verified.
        """
        return self._verified_dataset(manifest_path, need_candles=False).manifest

    def _verified_dataset(self, manifest_path: Path, *, need_candles: bool) -> _VerifiedDataset:
        """Return a byte-identical cached verification, otherwise verify and remember it."""
        cached = self._cached_dataset(manifest_path, need_candles=need_candles)
        if cached is not None:
            return cached
        dataset = self._verify_dataset(manifest_path)
        self._remember_dataset(manifest_path, dataset)
        return dataset

    def _cached_dataset(
        self, manifest_path: Path, *, need_candles: bool
    ) -> _VerifiedDataset | None:
        """Return a cached verification only while every verified file is byte-identical."""
        with self._content_lock:
            cached = self._content_cache.get(manifest_path)
            if cached is not None:
                self._content_cache.move_to_end(manifest_path)
        if cached is None or cached.identity is None or (need_candles and cached.candles is None):
            return None
        if self._identity_matches(cached.identity):
            return cached
        with self._content_lock:
            if self._content_cache.get(manifest_path) is cached:
                self._evict_dataset(manifest_path)
        return None

    def _remember_dataset(self, manifest_path: Path, dataset: _VerifiedDataset) -> None:
        """Cache one stable verification, keeping decoded candles only within the budget."""
        if dataset.identity is None:
            return
        candles = dataset.candles
        entry = (
            dataset
            if candles is not None and len(candles) <= self._candle_cache_budget
            else replace(dataset, candles=None)
        )
        with self._content_lock:
            self._evict_dataset(manifest_path)
            self._content_cache[manifest_path] = entry
            self._cached_candle_count += 0 if entry.candles is None else len(entry.candles)
            while self._content_cache and (
                len(self._content_cache) > _VERIFIED_DATASET_CACHE_ENTRIES
                or self._cached_candle_count > self._candle_cache_budget
            ):
                self._evict_dataset(next(iter(self._content_cache)))

    def _evict_dataset(self, manifest_path: Path) -> None:
        """Drop one cached verification; the caller holds ``_content_lock``."""
        evicted = self._content_cache.pop(manifest_path, None)
        if evicted is not None and evicted.candles is not None:
            self._cached_candle_count -= len(evicted.candles)

    def _verify_dataset(self, manifest_path: Path) -> _VerifiedDataset:
        """Fully verify one dataset and capture the file identities its verification read."""
        manifest_identity = _file_identity(manifest_path)
        try:
            payload = json.loads(manifest_path.read_text())
            manifest = self._manifest_from_payload(payload, manifest_path)
            files_identity = _files_identity(manifest.files)
            rows = tuple(row for file in manifest.files for row in _parquet_rows(file))
            candles = _rows_to_candles(rows)
            interval = _require_timeframe(manifest.timeframe)
            range_report = analyze_range(
                candles,
                interval,
                _parse_utc_text(manifest.starts_at),
                _parse_utc_text(manifest.ends_at),
                _parse_utc_text(manifest.ends_at) + interval.duration,
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
        schema_version = _require_supported_schema_version(
            cast("dict[str, object]", payload).get("schema_version")
        )
        expected = _fingerprint_from_manifest(
            manifest,
            _rows_for_fingerprint(rows, schema_version),
            schema_version=schema_version,
        )
        if manifest.content_fingerprint != f"sha256:{expected}":
            message = "Dataset verification failed because its content fingerprint does not match."
            raise DatasetStoreError(message)
        manifest = _with_verified_no_trade_count(
            manifest, cast("dict[str, object]", payload), count_no_trade_bars(candles)
        )
        identity = _dataset_identity(manifest)
        stable = (
            identity is not None
            and manifest_identity is not None
            and files_identity is not None
            and identity == (manifest_identity, *files_identity)
        )
        return _VerifiedDataset(
            manifest=manifest, candles=candles, identity=identity if stable else None
        )

    def _manifest_from_payload(
        self,
        payload: object,
        manifest_path: Path,
        *,
        resolve_file: Callable[[str], Path] | None = None,
    ) -> DatasetManifest:
        """Validate untrusted manifest JSON before using any referenced dataset file.

        ``resolve_file`` maps one manifest-relative path to a validated dataset path; the
        default is the full ``_safe_dataset_path`` check. Only the catalog listing passes
        a cached resolver.
        """
        resolve = resolve_file or (lambda item: _safe_dataset_path(self._root, item))
        if not isinstance(payload, dict):
            message = "Dataset verification failed because the manifest schema is unsupported."
            raise DatasetStoreError(message)
        manifest_payload = cast("dict[str, object]", payload)
        _require_supported_schema_version(manifest_payload.get("schema_version"))
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
        _require_volume_unit(manifest_payload, product_id)
        interval = _require_timeframe(timeframe)
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
        files = tuple(resolve(item) for item in cast("list[str]", files_value))
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
            or duration % interval.duration != timedelta(0)
            or expected_candle_count != duration // interval.duration
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
            synthetic_no_trade_intervals=_manifest_no_trade_count(
                manifest_payload, received_candle_count
            ),
        )
