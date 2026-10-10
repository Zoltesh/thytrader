"""Share identical venue reads within one execution-worker cycle (ADR 0131).

Many books trade the same products on the same clocks, and the cycle used to repeat the
same product-metadata, recent-preview and fee-tier reads once per book. Inside a bound
``CycleReads`` scope, ``shared_read`` performs a read once per key and hands the same
result to every later caller in that cycle. Keys name the exact request (a preview's key
includes the closed-bar boundary, so a bar that closes mid-cycle is read afresh). Failures
are never stored: the next caller reads again, exactly as before. Outside a scope (the API,
tests, other workers) every call reads the venue.

Range reads are deliberately not shared: the deploy-window cache re-fetches a sparse block
to confirm a missing bar (ADR 0095), and that second observation must be a real read.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

type ReadKey = tuple[str, ...]


class CycleReads:
    """Results of reads already made in one cycle, by exact request key."""

    def __init__(self) -> None:
        """Start the cycle with no shared reads."""
        self._values: dict[ReadKey, object] = {}
        self.hits = 0

    async def read[T](self, key: ReadKey, fetch: Callable[[], Awaitable[T]]) -> T:
        """Return this cycle's earlier result for ``key``, or fetch and remember it."""
        if key in self._values:
            self.hits += 1
            # The key names one request kind, and only ``fetch`` of that kind stores it.
            return cast("T", self._values[key])
        value = await fetch()
        self._values[key] = value
        return value


_CURRENT: ContextVar[CycleReads | None] = ContextVar("cycle_reads", default=None)


@contextmanager
def cycle_reads_scope(reads: CycleReads) -> Iterator[CycleReads]:
    """Share identical reads made in this context for the life of the scope."""
    token = _CURRENT.set(reads)
    try:
        yield reads
    finally:
        _CURRENT.reset(token)


async def shared_read[T](key: ReadKey, fetch: Callable[[], Awaitable[T]]) -> T:
    """Read once per key within the bound cycle; read every time outside one."""
    reads = _CURRENT.get()
    if reads is None:
        return await fetch()
    return await reads.read(key, fetch)
