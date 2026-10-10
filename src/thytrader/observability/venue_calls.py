"""Count and time venue REST calls made inside one bound scope (ADR 0131).

The execution worker binds one ``VenueCallLedger`` per cycle; the venue HTTP adapter
records every request into whichever ledger is bound in the calling context. Blocking
SDK calls run through ``asyncio.to_thread``, which copies the context, so a request made
on a worker thread lands in the cycle that issued it. With no ledger bound, recording is
a no-op. Only the method, a redacted endpoint shape, the outcome and the latency are kept;
never parameters, bodies, headers or identifiers.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING

from thytrader.observability.execution_cycle import (
    MAX_REPORTED_ENDPOINTS,
    VenueCallSummary,
    VenueEndpointTiming,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(slots=True)
class _Tally:
    """Running totals for one endpoint shape."""

    requests: int = 0
    errors: int = 0
    seconds: float = 0.0
    max_seconds: float = 0.0

    def add(self, seconds: float, *, ok: bool) -> None:
        """Count one request."""
        self.requests += 1
        self.errors += 0 if ok else 1
        self.seconds += seconds
        self.max_seconds = max(self.max_seconds, seconds)


@dataclass(frozen=True, slots=True)
class CallTotals:
    """A point-in-time count and summed latency, for per-book and per-phase deltas."""

    requests: int
    seconds: float

    def since(self, earlier: CallTotals) -> CallTotals:
        """Return the calls made between ``earlier`` and this reading."""
        return CallTotals(self.requests - earlier.requests, self.seconds - earlier.seconds)


class VenueCallLedger:
    """Thread-safe per-endpoint request counts and latency for one scope."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self._lock = threading.Lock()
        self._endpoints: dict[tuple[str, str], _Tally] = {}
        self._total = _Tally()

    def record(self, method: str, endpoint: str, seconds: float, *, ok: bool) -> None:
        """Record one finished request (``ok`` false for an HTTP error or transport failure)."""
        elapsed = max(seconds, 0.0)
        with self._lock:
            self._endpoints.setdefault((method, endpoint), _Tally()).add(elapsed, ok=ok)
            self._total.add(elapsed, ok=ok)

    def totals(self) -> CallTotals:
        """Return the running request count and summed latency."""
        with self._lock:
            return CallTotals(self._total.requests, self._total.seconds)

    def summary(self) -> VenueCallSummary:
        """Summarize the scope, listing the endpoints that took the most time first."""
        with self._lock:
            ranked = sorted(self._endpoints.items(), key=lambda item: (-item[1].seconds, item[0]))[
                :MAX_REPORTED_ENDPOINTS
            ]
            return VenueCallSummary(
                requests=self._total.requests,
                errors=self._total.errors,
                seconds=round(self._total.seconds, 3),
                max_seconds=round(self._total.max_seconds, 3),
                endpoints=tuple(
                    VenueEndpointTiming(
                        method=method,
                        endpoint=endpoint,
                        requests=tally.requests,
                        errors=tally.errors,
                        seconds=round(tally.seconds, 3),
                        max_seconds=round(tally.max_seconds, 3),
                    )
                    for (method, endpoint), tally in ranked
                ),
            )


_CURRENT: ContextVar[VenueCallLedger | None] = ContextVar("venue_call_ledger", default=None)


@contextmanager
def venue_call_scope(ledger: VenueCallLedger) -> Iterator[VenueCallLedger]:
    """Record venue calls made in this context (and threads it starts) into ``ledger``."""
    token = _CURRENT.set(ledger)
    try:
        yield ledger
    finally:
        _CURRENT.reset(token)


def record_venue_call(method: str, endpoint: str, seconds: float, *, ok: bool) -> None:
    """Record one request into the bound ledger; a no-op when none is bound."""
    ledger = _CURRENT.get()
    if ledger is not None:
        ledger.record(method, endpoint, seconds, ok=ok)
