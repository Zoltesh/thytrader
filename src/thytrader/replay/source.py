"""Installed Python source identity, distinct from historical research or container identity."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

MAX_SOURCE_BYTES = 16 * 1024 * 1024


def installed_source_identity() -> str:
    """Hash installed ThyTrader Python paths and bytes, never credentials or runtime account files.

    This is explicitly a Python-source build identity, not a Git commit or container digest.
    Original research identity remains missing unless its original exporter supplied it.
    """
    root = Path(__file__).resolve().parent.parent
    files = sorted(root.rglob("*.py"))
    inventory: list[tuple[str, str]] = []
    size = 0
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("installed source identity cannot follow external symlinks")
        size += path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise ValueError("installed source identity exceeds bounded Python source size")
        inventory.append((path.relative_to(root).as_posix(), sha256(path.read_bytes()).hexdigest()))
    content = json.dumps(inventory, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + sha256(content).hexdigest()
