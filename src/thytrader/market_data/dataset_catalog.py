"""Catalog listings of verified datasets.

:class:`_DatasetCatalog` lists every deep-verified dataset revision, and the newest
catalog-verified revision per provider, product and timeframe (ADR 0085), ranking cheap
manifest metadata first and re-checking only stat identities on warm listings.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from thytrader.market_data.dataset_content import _parse_utc_text
from thytrader.market_data.dataset_files import (
    _dataset_identity,
    _ListingStamp,
    _parquet_envelope_intact,
    _stamp_matches,
    _stat_identity,
    _StatIdentity,
)
from thytrader.market_data.dataset_manifest import (
    DatasetManifest,
    DatasetStoreError,
    _require_supported_schema_version,
)
from thytrader.market_data.dataset_validation import (
    _require_timeframe,
    _safe_dataset_path,
    _validate_identifier,
)
from thytrader.market_data.dataset_verification import (
    _FINGERPRINT,
    _DatasetCatalogCandidate,
    _DatasetVerifier,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class _DatasetCatalog(_DatasetVerifier):
    """List verified dataset revisions over the verifier's caches."""

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
