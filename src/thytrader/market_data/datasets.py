"""Immutable Parquet storage for validated historical candle ranges."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
from stat import S_ISREG
from threading import Lock, RLock
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import polars as pl

from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleRangeReport,
    interval_from_range,
    parse_candle_interval,
)
from thytrader.market_data.no_trade import count_no_trade_bars
from thytrader.market_data.quality import (
    CandleQualityError,
    analyze_range,
    validate_candle_values,
)
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


type _FileIdentity = tuple[Path, int, int, int, str]
# Device, inode, size, mtime_ns, ctime_ns of one regular file, captured without reading it.
type _StatIdentity = tuple[int, int, int, int, int]
type _ListingStamp = tuple[tuple[Path, _StatIdentity], ...]


_DATASET_SCHEMA_VERSION = 2
# Verified datasets kept per process. Each hit is re-checked against the stat identity and
# SHA-256 digest of the manifest and every Parquet file, so a cached entry never serves
# bytes that differ from the bytes that were verified.
_VERIFIED_DATASET_CACHE_ENTRIES = 256
DEFAULT_VERIFIED_CANDLE_CACHE_BUDGET = 120_000
_PARQUET_MAGIC = b"PAR1"
# Header magic, footer length, and footer magic: the smallest possible complete file.
_PARQUET_MINIMUM_BYTES = 12
_SUPPORTED_DATASET_SCHEMA_VERSIONS = frozenset({1, 2})
_FINGERPRINT_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_FINGERPRINT = re.compile(r"^sha256:([0-9a-f]{64})$")


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


class DatasetStore:
    """Write and verify complete validated ranges as immutable date-partitioned datasets."""

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

    def list_verified(self) -> tuple[DatasetManifest, ...]:
        """Return every complete immutable dataset whose manifest re-verifies from disk."""
        with self._catalog_lock:
            return self._list_verified()

    def _list_verified(self) -> tuple[DatasetManifest, ...]:
        """List full verified history while the shared catalog cache lock is held."""
        manifests = self._root / "manifests"
        if not manifests.exists():
            self._verified_cache.clear()
            return ()
        verified: list[DatasetManifest] = []
        seen: set[Path] = set()
        for path in sorted(manifests.glob("*.json"), reverse=True):
            seen.add(path)
            manifest = self._list_verified_manifest(path)
            if manifest is not None:
                verified.append(manifest)
        for cached_path in list(self._verified_cache):
            if cached_path not in seen:
                del self._verified_cache[cached_path]
        return tuple(verified)

    def list_latest_verified(self) -> tuple[DatasetManifest, ...]:
        """Return the newest catalog-verified revision per provider/product/timeframe.

        This is a catalog listing for selection and display (ADR 0085). Each entry passed
        structural verification: manifest schema and facts, its canonical content address,
        safe in-market file paths, distinct files, every file present, and an intact Parquet
        envelope. Parquet rows are not decoded here. Anything that binds a dataset to a run
        resolves its exact fingerprint through ``load_manifest`` or ``load_candles``, which
        re-verify the content fingerprint, so a corrupt listed revision fails closed there.
        """
        with self._catalog_lock:
            return self._list_latest_verified()

    def _list_latest_verified(self) -> tuple[DatasetManifest, ...]:
        """List latest catalog-verified revisions while the shared catalog lock is held.

        Manifest metadata is cheap to inspect, so candidates are grouped and ordered first.
        Each market then structurally verifies newest-first until one revision passes.
        Warm listings only re-stat the manifest and files of each cached newest revision.
        Historical revisions stay available through ``list_verified()``.
        """
        manifests = self._root / "manifests"
        if not manifests.exists():
            self._verified_cache.clear()
            self._listing_cache.clear()
            self._envelope_cache.clear()
            return ()
        candidates: dict[tuple[str, str, str], list[_DatasetCatalogCandidate]] = {}
        seen: set[Path] = set()
        for path in manifests.glob("*.json"):
            seen.add(path)
            try:
                candidate = self._cached_catalog_candidate(path)
            except DatasetStoreError:
                self._verified_cache.pop(path, None)
                self._candidate_cache.pop(path, None)
                continue
            key = (candidate.provider, candidate.product_id, candidate.timeframe)
            candidates.setdefault(key, []).append(candidate)
        self._prune_catalog_caches(seen)

        latest: list[DatasetManifest] = []
        for key in sorted(candidates):
            revisions = candidates[key]
            revisions.sort(key=lambda candidate: candidate.starts_at)
            revisions.sort(key=lambda candidate: candidate.ends_at, reverse=True)
            for candidate in revisions:
                entry = self._catalog_entry(candidate.manifest_path)
                if entry is not None:
                    latest.append(entry)
                    break
        referenced = {path for manifest in latest for path in manifest.files}
        self._envelope_cache = {
            path: identity for path, identity in self._envelope_cache.items() if path in referenced
        }
        self._catalog_paths = {
            relative: path for relative, path in self._catalog_paths.items() if path in referenced
        }
        return tuple(latest)

    def _prune_catalog_caches(self, seen: set[Path]) -> None:
        """Drop cached verification and ranking entries for manifests no longer on disk."""
        for cached_path in list(self._verified_cache):
            if cached_path not in seen:
                del self._verified_cache[cached_path]
        for cached_path in list(self._candidate_cache):
            if cached_path not in seen:
                del self._candidate_cache[cached_path]
        for cached_path in list(self._listing_cache):
            if cached_path not in seen:
                del self._listing_cache[cached_path]

    def _catalog_entry(self, manifest_path: Path) -> DatasetManifest | None:
        """Return one catalog-verified revision, or None when it fails structural checks."""
        cached = self._listing_cache.get(manifest_path)
        if cached is not None and _stamp_matches(cached[0]):
            return cached[1]
        self._listing_cache.pop(manifest_path, None)
        try:
            manifest = self._load_manifest_metadata(
                manifest_path, resolve_file=self._catalog_file_path
            )
        except DatasetStoreError:
            return None
        stamp = self._catalog_stamp(manifest)
        if stamp is None:
            return None
        self._listing_cache[manifest_path] = (stamp, manifest)
        return manifest

    def _catalog_file_path(self, relative: str) -> Path:
        """Validate one manifest-relative partition path once per listing cache lifetime.

        Successive cumulative revisions name mostly the same immutable partitions, so a
        new revision only resolves its new files. Binding paths never use this cache.
        """
        cached = self._catalog_paths.get(relative)
        if cached is not None:
            return cached
        path = _safe_dataset_path(self._root, relative)
        self._catalog_paths[relative] = path
        return path

    def _catalog_stamp(self, manifest: DatasetManifest) -> _ListingStamp | None:
        """Check file uniqueness, placement, presence, and envelopes; return their identities."""
        if len(set(manifest.files)) != len(manifest.files):
            return None
        market_root = self._root / manifest.provider / manifest.product_id / manifest.timeframe
        manifest_identity = _stat_identity(manifest.manifest_path)
        if manifest_identity is None:
            return None
        entries: list[tuple[Path, _StatIdentity]] = [(manifest.manifest_path, manifest_identity)]
        for path in manifest.files:
            identity = _stat_identity(path)
            if identity is None or not path.is_relative_to(market_root):
                return None
            if self._envelope_cache.get(path) != identity:
                if not _parquet_envelope_intact(path, identity[2]):
                    return None
                self._envelope_cache[path] = identity
            entries.append((path, identity))
        return tuple(entries)

    def _cached_catalog_candidate(self, manifest_path: Path) -> _DatasetCatalogCandidate:
        """Return ranking metadata, re-parsing the manifest only when its stat identity changes."""
        try:
            stat = manifest_path.stat()
        except OSError as error:
            message = "Dataset verification failed while reading its manifest."
            raise DatasetStoreError(message) from error
        stamp = (stat.st_size, stat.st_mtime_ns)
        cached = self._candidate_cache.get(manifest_path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        candidate = self._load_catalog_candidate(manifest_path)
        self._candidate_cache[manifest_path] = (stamp, candidate)
        return candidate

    def _load_catalog_candidate(self, manifest_path: Path) -> _DatasetCatalogCandidate:
        """Validate only metadata needed to rank one deep-verification candidate."""
        try:
            payload = json.loads(manifest_path.read_text())
        except (OSError, OverflowError, ValueError, json.JSONDecodeError) as error:
            message = "Dataset verification failed while reading its manifest."
            raise DatasetStoreError(message) from error
        return self._catalog_candidate_from_payload(payload, manifest_path)

    def _catalog_candidate_from_payload(
        self, payload: object, manifest_path: Path
    ) -> _DatasetCatalogCandidate:
        """Build a ranking candidate without inspecting its cumulative file list."""
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
        _require_timeframe(timeframe)
        fingerprint_match = _FINGERPRINT.fullmatch(content_fingerprint)
        if fingerprint_match is None:
            message = "Dataset verification failed because the content fingerprint is malformed."
            raise DatasetStoreError(message)
        expected_path = self._root / "manifests" / f"{fingerprint_match.group(1)}.json"
        if manifest_path.resolve() != expected_path.resolve():
            message = (
                "Dataset verification failed: manifest is not at its canonical publication path."
            )
            raise DatasetStoreError(message)
        starts_at = _parse_utc_text(cast("str", manifest_payload["starts_at"]))
        ends_at = _parse_utc_text(cast("str", manifest_payload["ends_at"]))
        if starts_at >= ends_at:
            message = "Dataset verification failed because manifest facts are inconsistent."
            raise DatasetStoreError(message)
        return _DatasetCatalogCandidate(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            starts_at=starts_at,
            ends_at=ends_at,
            manifest_path=manifest_path,
        )

    def _load_manifest_metadata(
        self,
        manifest_path: Path,
        *,
        resolve_file: Callable[[str], Path] | None = None,
    ) -> DatasetManifest:
        """Validate one manifest structure without reading its referenced Parquet content."""
        try:
            payload = json.loads(manifest_path.read_text())
            return self._manifest_from_payload(payload, manifest_path, resolve_file=resolve_file)
        except DatasetStoreError:
            raise
        except (OSError, OverflowError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            message = "Dataset verification failed while reading its manifest."
            raise DatasetStoreError(message) from error

    def _list_verified_manifest(self, manifest_path: Path) -> DatasetManifest | None:
        """Return one listing entry only when verification and cached identity are coherent."""
        cached = self._verified_cache.get(manifest_path)
        if cached is not None:
            (identity, manifest) = cached
            if self._identity_matches(identity):
                return manifest
        try:
            candidate = self._load_manifest_metadata(manifest_path)
        except DatasetStoreError:
            self._verified_cache.pop(manifest_path, None)
            return None
        identity_before = _dataset_identity(candidate)
        if identity_before is None:
            self._verified_cache.pop(manifest_path, None)
            return None
        try:
            manifest = self.load_verified(manifest_path)
        except DatasetStoreError:
            self._verified_cache.pop(manifest_path, None)
            return None
        identity_after = _dataset_identity(manifest)
        if identity_after is None or identity_after != identity_before:
            self._verified_cache.pop(manifest_path, None)
            return None
        self._verified_cache[manifest_path] = (identity_after, manifest)
        return manifest

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


def _dataset_identity(
    manifest: DatasetManifest,
) -> tuple[_FileIdentity, ...] | None:
    """Capture one coherent identity snapshot for a manifest and all of its files."""
    identities: list[_FileIdentity] = []
    for path in (manifest.manifest_path, *manifest.files):
        identity = _file_identity(path)
        if identity is None:
            return None
        identities.append(identity)
    return tuple(identities)


def _files_identity(files: tuple[Path, ...]) -> tuple[_FileIdentity, ...] | None:
    """Capture content identities for a manifest's files, or miss when any is unreadable."""
    identities: list[_FileIdentity] = []
    for path in files:
        identity = _file_identity(path)
        if identity is None:
            return None
        identities.append(identity)
    return tuple(identities)


def _stat_identity(path: Path) -> _StatIdentity | None:
    """Return a regular file's device, inode, size, and change stamps without reading it."""
    try:
        status = path.stat()
    except OSError:
        return None
    if not S_ISREG(status.st_mode):
        return None
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        int(status.st_mtime_ns),
        int(status.st_ctime_ns),
    )


def _stamp_matches(stamp: _ListingStamp) -> bool:
    """True while every file in a catalog stamp keeps its captured stat identity."""
    return all(_stat_identity(path) == identity for path, identity in stamp)


def _parquet_envelope_intact(path: Path, size: int) -> bool:
    """True when a file starts and ends with the Parquet magic, so it is not truncated."""
    if size < _PARQUET_MINIMUM_BYTES:
        return False
    try:
        with path.open("rb") as file:
            head = file.read(len(_PARQUET_MAGIC))
            file.seek(-len(_PARQUET_MAGIC), os.SEEK_END)
            tail = file.read(len(_PARQUET_MAGIC))
    except OSError:
        return False
    return head == _PARQUET_MAGIC and tail == _PARQUET_MAGIC


def _file_identity(path: Path) -> _FileIdentity | None:
    """Capture one regular file's metadata and content digest, or miss when either fails.

    Size, modification time, and change time are not sufficient: an in-place same-size
    rewrite can restore mtime while ctime stays unchanged in the same timestamp tick.
    The digest is what makes that replacement a cache miss.
    """
    try:
        first = path.stat()
    except OSError:
        return None
    if not S_ISREG(first.st_mode):
        return None
    digest = _file_content_digest(path)
    if digest is None:
        return None
    try:
        second = path.stat()
    except OSError:
        return None
    if not _same_regular_file_metadata(first, second):
        return None
    return (path, second.st_size, int(second.st_mtime_ns), int(second.st_ctime_ns), digest)


def _same_regular_file_metadata(first: os.stat_result, second: os.stat_result) -> bool:
    """Return True when two stat snapshots describe the same regular file metadata."""
    return (
        S_ISREG(second.st_mode)
        and first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
        and int(first.st_mtime_ns) == int(second.st_mtime_ns)
        and int(first.st_ctime_ns) == int(second.st_ctime_ns)
    )


def _file_content_digest(path: Path) -> str | None:
    """Return a SHA-256 hex digest of one file's bytes, or miss when the file cannot be read."""
    try:
        with path.open("rb") as file:
            return file_digest(file, "sha256").hexdigest()
    except OSError:
        return None


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


def _require_supported_schema_version(value: object) -> int:
    """Reject manifests whose schema version is outside the supported immutable set."""
    if not isinstance(value, int) or value not in _SUPPORTED_DATASET_SCHEMA_VERSIONS:
        message = "Dataset verification failed because the manifest schema is unsupported."
        raise DatasetStoreError(message)
    return value


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
    return payload


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


def _utc_text(value: datetime) -> str:
    """Serialize an aware timestamp in canonical UTC RFC3339 form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
