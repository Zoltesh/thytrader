"""Serialize live deployment starts in the creating process (ADR 0134 P2-7).

A live start reads the fleet and the venue, then inserts the book. Two concurrent live
futures starts could both pass product exclusivity (I3) and the capital envelope from the same
reads. Live books are created only by the API process, so holding one lock per event loop from
the checks to the insert makes the read-then-insert atomic. Paper starts are not serialized.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
import weakref

from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


@asynccontextmanager
async def live_start_serialized(mode: DeploymentMode) -> AsyncIterator[None]:
    """Hold the process's live start lock for a live start; paper passes straight through."""
    if mode is not DeploymentMode.LIVE:
        yield
        return
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    async with lock:
        yield
