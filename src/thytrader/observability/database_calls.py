"""Count and time database statements made inside one bound scope (ADR 0131).

The execution worker binds one ``DatabaseCallLedger`` per cycle; SQLAlchemy cursor events
record each statement's count and latency into whichever ledger is bound in the calling
context. No SQL text, parameters or rows are kept.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
from typing import TYPE_CHECKING

from thytrader.observability.venue_calls import CallTotals

if TYPE_CHECKING:
    from collections.abc import Iterator


class DatabaseCallLedger:
    """Thread-safe statement count and summed latency for one scope."""

    def __init__(self) -> None:
        """Start with no recorded statements."""
        self._lock = threading.Lock()
        self._statements = 0
        self._seconds = 0.0

    def record(self, seconds: float) -> None:
        """Record one finished statement."""
        with self._lock:
            self._statements += 1
            self._seconds += max(seconds, 0.0)

    def totals(self) -> CallTotals:
        """Return the running statement count and summed latency."""
        with self._lock:
            return CallTotals(self._statements, self._seconds)


_CURRENT: ContextVar[DatabaseCallLedger | None] = ContextVar("database_call_ledger", default=None)


@contextmanager
def database_call_scope(ledger: DatabaseCallLedger) -> Iterator[DatabaseCallLedger]:
    """Record database statements made in this context into ``ledger``."""
    token = _CURRENT.set(ledger)
    try:
        yield ledger
    finally:
        _CURRENT.reset(token)


def record_database_call(seconds: float) -> None:
    """Record one statement into the bound ledger; a no-op when none is bound."""
    ledger = _CURRENT.get()
    if ledger is not None:
        ledger.record(seconds)
