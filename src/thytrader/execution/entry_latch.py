"""Process-local view of the durable fleet entry-inhibition latch.

The execution loop already asks :func:`thytrader.execution.lifecycle.entries_allowed`
before a new entry or a risk-increasing reprice. That function is synchronous, so
this module holds the latest snapshot read by the execution worker. An empty cache
means this process has not proven entry admission and therefore fails closed.
Once a snapshot is remembered, a missing mode fails closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.execution.models import DeploymentMode


class _LatchCache:
    """Process-wide holder for the latest latch snapshot.

    A module-level object avoids a ``global`` statement: the execution loop's
    synchronous admission path reads whatever the worker last refreshed.
    """

    def __init__(self) -> None:
        """Start unloaded so boot/restart cannot admit entries before a durable read."""
        self.snapshot: dict[str, bool] | None = None


_cache = _LatchCache()


def process_entry_inhibited(mode: DeploymentMode) -> bool:
    """Return whether this process's latest snapshot inhibits one mode.

    ``True`` when no snapshot has been loaded. Callers that admit risk must
    refresh the snapshot first; the durable store remains authoritative for
    starts and intent persistence.
    """
    snapshot = _cache.snapshot
    if snapshot is None:
        return True
    return snapshot.get(mode.value, True)


def remember_entry_inhibition(snapshot: dict[str, bool] | None) -> None:
    """Replace the process snapshot. ``None`` clears it back to unknown."""
    _cache.snapshot = None if snapshot is None else dict(snapshot)


def entry_inhibition_snapshot() -> dict[str, bool] | None:
    """Return a copy of the process snapshot, or ``None`` when unloaded."""
    snapshot = _cache.snapshot
    if snapshot is None:
        return None
    return dict(snapshot)


def clear_entry_inhibition_cache() -> None:
    """Drop the process snapshot so a test cannot leak inhibition."""
    remember_entry_inhibition(None)
