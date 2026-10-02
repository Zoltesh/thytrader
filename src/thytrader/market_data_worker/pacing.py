"""Shared request pacing for the market-data worker's provider calls.

One pacer serves every ingest target in the worker process, because Coinbase rate
limits are per API key and IP, not per product. Requests are spaced by a small fixed
pause. An HTTP 429 sets a cooldown that every target waits out before its next
request, doubling on consecutive throttles up to a ceiling.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# A quarter second between provider calls keeps the worker near four range requests
# (eight Coinbase HTTP calls with the product check) per second. That leaves most of
# the shared per-key budget to the API and execution worker.
PROVIDER_REQUEST_PAUSE_SECONDS = 0.25
RATE_LIMIT_COOLDOWN_INITIAL_SECONDS = 2.0
RATE_LIMIT_COOLDOWN_MAX_SECONDS = 60.0


class ProviderPacer:
    """Space provider requests and back off every target together after a throttle."""

    def __init__(
        self,
        *,
        pause_seconds: float = 0.0,
        stop_requested: asyncio.Event | None = None,
        cooldown_initial_seconds: float = RATE_LIMIT_COOLDOWN_INITIAL_SECONDS,
        cooldown_max_seconds: float = RATE_LIMIT_COOLDOWN_MAX_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure spacing; the default of no pause suits direct, hermetic calls."""
        self._pause = max(0.0, pause_seconds)
        self._stop = stop_requested
        self._cooldown_initial = max(0.0, cooldown_initial_seconds)
        self._cooldown_max = max(self._cooldown_initial, cooldown_max_seconds)
        self._clock = clock
        self._next_request_at = 0.0
        self._cooldown = 0.0
        self.granted = 0
        self.throttled = 0

    @property
    def cooldown_seconds(self) -> float:
        """Return the current rate-limit cooldown, zero after a successful request."""
        return self._cooldown

    async def acquire(self) -> bool:
        """Wait for the next request slot; return False when shutdown arrives meanwhile."""
        delay = self._next_request_at - self._clock()
        if delay > 0 and not await self._sleep(delay):
            return False
        if self._stop is not None and self._stop.is_set():
            return False
        self.granted += 1
        return True

    def completed(self) -> None:
        """Record a finished non-throttled request and schedule the next slot after the pause."""
        self._cooldown = 0.0
        self._next_request_at = max(self._next_request_at, self._clock() + self._pause)

    def throttled_by_provider(self) -> float:
        """Record an HTTP 429, push every target's next slot out, and return the cooldown."""
        self.throttled += 1
        self._cooldown = min(
            self._cooldown_max,
            max(self._cooldown_initial, self._cooldown * 2),
        )
        self._next_request_at = max(self._next_request_at, self._clock() + self._cooldown)
        return self._cooldown

    async def _sleep(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; return False when the stop event fires first."""
        if self._stop is None:
            await asyncio.sleep(seconds)
            return True
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        return not self._stop.is_set()
