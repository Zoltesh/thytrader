"""Bounded garbage collection of superseded immutable dataset revisions.

The market-data worker publishes a new cumulative revision for every ingest chunk, and
:meth:`DatasetStore.extend` reuses unchanged day partitions from the prior revision.
Superseded revisions therefore pile up as manifests that no reader needs while their
Parquet files may still be shared with newer revisions.

A manifest is deleted only when every condition holds:

* it is not referenced by any persisted record (``DatasetReferenceSource``);
* it is not a maximal revision of its provider/product/timeframe: some other manifest of
  the same target covers its whole half-open range (strictly wider, or the same range
  published later), so the newest revision and the final revision of every older island
  are always kept;
* the earliest manifest that covers it was published at least ``grace`` ago, so a reader
  that resolved it as "latest" moments before supersession has time to bind it;
* a kept, maximal covering manifest passes full verification (files present and hashed).

Manifests are unpublished first; a Parquet file is removed only when no surviving
manifest lists it. Any unreadable manifest, reference-scan failure, or concurrent pass
aborts the whole pass without deleting anything. Candles are never rewritten.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import fcntl
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DEFAULT_RETENTION_GRACE = timedelta(hours=24)
DEFAULT_MAX_MANIFESTS_PER_PASS = 5_000
_MANIFEST_NAME = re.compile(r"^([0-9a-f]{64})\.json$")
_LOCK_NAME = ".retention.lock"

type RetentionAbortReason = Literal[
    "unreadable_manifests",
    "reference_scan_failed",
    "pass_in_progress",
]


@runtime_checkable
class DatasetReferenceSource(Protocol):
    """Every dataset fingerprint that a persisted record still references."""

    async def referenced_fingerprints(self) -> frozenset[str]:
        """Return ``sha256:<hex>`` fingerprints that must never be collected."""
        ...


@dataclass(frozen=True, slots=True)
class _ManifestFacts:
    """Cheaply parsed manifest identity, range, files, and publication instant."""

    fingerprint: str
    target: tuple[str, str, str]
    starts_at: datetime
    ends_at: datetime
    files: tuple[str, ...]
    path: Path
    published_at: datetime


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """Redacted counts for one retention pass; contains no candle data or paths."""

    dry_run: bool
    scanned_manifests: int
    unreadable_manifests: int
    referenced_fingerprints: int
    retained_manifests: int
    eligible_manifests: int
    deleted_manifests: int
    deleted_files: int
    freed_bytes: int
    truncated: bool
    aborted: RetentionAbortReason | None = None
    deleted_by_target: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """Return one stable single-line summary for logs and audit detail."""
        state = f"aborted={self.aborted}" if self.aborted is not None else "aborted=none"
        return (
            f"dry_run={str(self.dry_run).lower()} {state} scanned={self.scanned_manifests} "
            f"unreadable={self.unreadable_manifests} "
            f"referenced={self.referenced_fingerprints} retained={self.retained_manifests} "
            f"eligible={self.eligible_manifests} deleted_manifests={self.deleted_manifests} "
            f"deleted_files={self.deleted_files} freed_bytes={self.freed_bytes} "
            f"truncated={str(self.truncated).lower()}"
        )

    def as_payload(self) -> dict[str, object]:
        """Serialize the report for the one-shot CLI."""
        return {
            "dry_run": self.dry_run,
            "aborted": self.aborted,
            "scanned_manifests": self.scanned_manifests,
            "unreadable_manifests": self.unreadable_manifests,
            "referenced_fingerprints": self.referenced_fingerprints,
            "retained_manifests": self.retained_manifests,
            "eligible_manifests": self.eligible_manifests,
            "deleted_manifests": self.deleted_manifests,
            "deleted_files": self.deleted_files,
            "freed_bytes": self.freed_bytes,
            "truncated": self.truncated,
            "deleted_by_target": dict(sorted(self.deleted_by_target.items())),
        }


async def collect_superseded_datasets(
    *,
    root: Path,
    references: DatasetReferenceSource,
    verify: Callable[[Path], bool],
    now: datetime,
    dry_run: bool,
    grace: timedelta = DEFAULT_RETENTION_GRACE,
    max_manifests: int = DEFAULT_MAX_MANIFESTS_PER_PASS,
) -> RetentionReport:
    """Run one bounded retention pass over ``root``; see the module docstring for rules."""
    if max_manifests < 1:
        message = "Dataset retention needs a positive per-pass manifest bound."
        raise ValueError(message)
    if now.tzinfo is not UTC:
        message = "Dataset retention requires a timezone-aware UTC instant."
        raise ValueError(message)
    with _exclusive_pass(root) as acquired:
        if not acquired:
            return _aborted(dry_run, "pass_in_progress")
        manifests, unreadable = await asyncio.to_thread(_scan_manifests, root)
        if unreadable:
            return _aborted(dry_run, "unreadable_manifests", len(manifests), unreadable)
        try:
            referenced = await references.referenced_fingerprints()
        except Exception:  # noqa: BLE001 - an unknown reference set must delete nothing.
            return _aborted(dry_run, "reference_scan_failed", len(manifests))
        return await asyncio.to_thread(
            _collect,
            root=root,
            manifests=manifests,
            referenced=referenced,
            verify=verify,
            cutoff=now - grace,
            dry_run=dry_run,
            max_manifests=max_manifests,
        )


def _aborted(
    dry_run: bool,
    reason: RetentionAbortReason,
    scanned: int = 0,
    unreadable: int = 0,
) -> RetentionReport:
    """Return a report for a pass that deleted nothing."""
    return RetentionReport(
        dry_run=dry_run,
        scanned_manifests=scanned,
        unreadable_manifests=unreadable,
        referenced_fingerprints=0,
        retained_manifests=scanned,
        eligible_manifests=0,
        deleted_manifests=0,
        deleted_files=0,
        freed_bytes=0,
        truncated=False,
        aborted=reason,
    )


@contextlib.contextmanager
def _exclusive_pass(root: Path) -> Iterator[bool]:
    """Hold a non-blocking advisory lock so two passes never interleave deletions."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / _LOCK_NAME).open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _scan_manifests(root: Path) -> tuple[tuple[_ManifestFacts, ...], int]:
    """Parse every published manifest; count (never delete) unreadable ones."""
    directory = root / "manifests"
    if not directory.is_dir():
        return (), 0
    facts: list[_ManifestFacts] = []
    unreadable = 0
    for path in sorted(directory.iterdir()):
        match = _MANIFEST_NAME.fullmatch(path.name)
        if match is None:
            continue
        parsed = _parse_manifest(path, match.group(1))
        if parsed is None:
            unreadable += 1
        else:
            facts.append(parsed)
    return tuple(facts), unreadable


def _parse_manifest(path: Path, digest: str) -> _ManifestFacts | None:
    """Return validated identity facts, or None for any malformed or mismatched manifest."""
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
        published_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    text = {
        key: payload.get(key)
        for key in ("provider", "product_id", "timeframe", "starts_at", "ends_at")
    }
    files = payload.get("files")
    fingerprint = payload.get("content_fingerprint")
    if (
        any(not isinstance(value, str) or not value for value in text.values())
        or fingerprint != f"sha256:{digest}"
        or not isinstance(files, list)
        or not files
        or not all(isinstance(item, str) and _is_relative_file(item) for item in files)
    ):
        return None
    try:
        starts_at = _parse_instant(str(text["starts_at"]))
        ends_at = _parse_instant(str(text["ends_at"]))
    except ValueError:
        return None
    if starts_at >= ends_at:
        return None
    return _ManifestFacts(
        fingerprint=f"sha256:{digest}",
        target=(str(text["provider"]), str(text["product_id"]), str(text["timeframe"])),
        starts_at=starts_at,
        ends_at=ends_at,
        files=tuple(str(item) for item in files),
        path=path,
        published_at=published_at,
    )


def _is_relative_file(item: str) -> bool:
    """Reject absolute or escaping file entries so deletion stays inside the root."""
    candidate = Path(item)
    return not candidate.is_absolute() and ".." not in candidate.parts and bool(candidate.parts)


def _parse_instant(value: str) -> datetime:
    """Parse one manifest RFC3339 instant into aware UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        message = "Manifest instant is not timezone-aware."
        raise ValueError(message)
    return parsed.astimezone(UTC)


def _covers(outer: _ManifestFacts, inner: _ManifestFacts) -> bool:
    """True when ``outer`` contains ``inner``'s range and supersedes it."""
    if outer.fingerprint == inner.fingerprint:
        return False
    if outer.starts_at > inner.starts_at or outer.ends_at < inner.ends_at:
        return False
    same_range = outer.starts_at == inner.starts_at and outer.ends_at == inner.ends_at
    return not same_range or outer.published_at > inner.published_at


def _collect(
    *,
    root: Path,
    manifests: tuple[_ManifestFacts, ...],
    referenced: frozenset[str],
    verify: Callable[[Path], bool],
    cutoff: datetime,
    dry_run: bool,
    max_manifests: int,
) -> RetentionReport:
    """Select eligible superseded manifests and delete them oldest-first within the bound."""
    by_target: dict[tuple[str, str, str], list[_ManifestFacts]] = {}
    for manifest in manifests:
        by_target.setdefault(manifest.target, []).append(manifest)
    verified: dict[str, bool] = {}
    eligible: list[_ManifestFacts] = []
    for group in by_target.values():
        eligible.extend(_eligible_in_group(group, referenced, verify, verified, cutoff))
    eligible.sort(key=lambda item: (item.published_at, item.fingerprint))
    selected = eligible[:max_manifests]
    deleted_by_target: dict[str, int] = {}
    for manifest in selected:
        key = "/".join(manifest.target)
        deleted_by_target[key] = deleted_by_target.get(key, 0) + 1
    deleted_files, freed = (0, 0) if dry_run else _delete(root, manifests, selected)
    return RetentionReport(
        dry_run=dry_run,
        scanned_manifests=len(manifests),
        unreadable_manifests=0,
        referenced_fingerprints=len(referenced),
        retained_manifests=len(manifests) - len(selected),
        eligible_manifests=len(eligible),
        deleted_manifests=0 if dry_run else len(selected),
        deleted_files=deleted_files,
        freed_bytes=freed,
        truncated=len(eligible) > len(selected),
        deleted_by_target=deleted_by_target,
    )


def _eligible_in_group(
    group: list[_ManifestFacts],
    referenced: frozenset[str],
    verify: Callable[[Path], bool],
    verified: dict[str, bool],
    cutoff: datetime,
) -> Iterator[_ManifestFacts]:
    """Yield superseded, unreferenced manifests anchored by a verified maximal revision.

    One sweep in (start asc, end desc, published desc) order: every manifest that can
    cover an item precedes it, so tracking the widest-ending predecessor answers
    "is it covered" (any), "was it superseded before the cutoff" (old), and "which
    maximal revision anchors it" in O(n log n). Ties resolve towards keeping.
    """
    ordered = sorted(
        group,
        key=lambda item: (
            item.starts_at,
            -item.ends_at.timestamp(),
            -item.published_at.timestamp(),
        ),
    )
    newest = max(
        group,
        key=lambda item: (item.ends_at, -item.starts_at.timestamp(), item.published_at),
    )
    best_any: _ManifestFacts | None = None
    best_old: _ManifestFacts | None = None
    best_maximal: _ManifestFacts | None = None
    for manifest in ordered:
        covered = best_any is not None and _covers(best_any, manifest)
        if not covered:
            best_maximal = _wider(best_maximal, manifest)
        elif (
            manifest.fingerprint != newest.fingerprint
            and manifest.fingerprint not in referenced
            and best_old is not None
            and _covers(best_old, manifest)
            and best_maximal is not None
            and _covers(best_maximal, manifest)
            and _verified(best_maximal, verify, verified)
        ):
            yield manifest
        best_any = _wider(best_any, manifest)
        if manifest.published_at <= cutoff:
            best_old = _wider(best_old, manifest)


def _wider(current: _ManifestFacts | None, candidate: _ManifestFacts) -> _ManifestFacts:
    """Keep the predecessor with the latest end, preferring the later publication."""
    if current is None:
        return candidate
    if (candidate.ends_at, candidate.published_at) > (current.ends_at, current.published_at):
        return candidate
    return current


def _verified(
    manifest: _ManifestFacts, verify: Callable[[Path], bool], cache: dict[str, bool]
) -> bool:
    """Verify one anchor at most once per pass; any verifier error counts as unverified."""
    if manifest.fingerprint not in cache:
        try:
            cache[manifest.fingerprint] = verify(manifest.path)
        except Exception:  # noqa: BLE001 - an unverifiable anchor protects what it covers.
            cache[manifest.fingerprint] = False
    return cache[manifest.fingerprint]


def _delete(
    root: Path,
    manifests: tuple[_ManifestFacts, ...],
    selected: list[_ManifestFacts],
) -> tuple[int, int]:
    """Unpublish selected manifests, then remove files no surviving manifest lists."""
    doomed = {item.fingerprint for item in selected}
    surviving_files = {
        file for item in manifests if item.fingerprint not in doomed for file in item.files
    }
    candidate_files: set[str] = set()
    freed = 0
    for manifest in selected:
        with contextlib.suppress(FileNotFoundError):
            freed += manifest.path.stat().st_size
            manifest.path.unlink()
        candidate_files.update(manifest.files)
    deleted_files = 0
    for relative in sorted(candidate_files - surviving_files):
        path = root / relative
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        deleted_files += 1
        freed += size
        _prune_empty_parents(path.parent, root)
    return deleted_files, freed


def _prune_empty_parents(directory: Path, root: Path) -> None:
    """Remove now-empty day/month/year directories without ever leaving the root."""
    current = directory
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
