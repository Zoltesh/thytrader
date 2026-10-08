"""On-disk identity, integrity, and durability helpers for dataset files.

Stat and content-digest identities decide whether a cached verification still describes
the bytes on disk; the Parquet envelope check rejects truncated files; ``fsync`` helpers
make publication renames durable. Used by :class:`thytrader.market_data.datasets.DatasetStore`.
"""

from __future__ import annotations

from hashlib import file_digest
import os
from stat import S_ISREG
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.market_data.dataset_manifest import DatasetManifest


type _FileIdentity = tuple[Path, int, int, int, str]
# Device, inode, size, mtime_ns, ctime_ns of one regular file, captured without reading it.
type _StatIdentity = tuple[int, int, int, int, int]
type _ListingStamp = tuple[tuple[Path, _StatIdentity], ...]
_PARQUET_MAGIC = b"PAR1"
# Header magic, footer length, and footer magic: the smallest possible complete file.
_PARQUET_MINIMUM_BYTES = 12


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
