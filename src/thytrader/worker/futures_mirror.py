"""CFM futures account mirror loop for the worker process (ADR 0127, slice P0-5).

Every 60 seconds the mirror reads the CFM balance summary, positions, intraday margin
setting and current margin window through the GET-only adapter and appends one snapshot.
Each read fails independently and is recorded as failure evidence; a failed read is
unknown, never an empty or zero value. Enablement is ``enabled`` only when the balance
summary parsed. Nothing here can place, cancel or close an order, sweep funds or change a
margin setting.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountReadError,
    FuturesAccountStoreUnavailableError,
    FuturesEnablement,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.exchanges.protocols import FuturesAccountReader

_logger = logging.getLogger(__name__)
FUTURES_MIRROR_INTERVAL_SECONDS = 60.0


async def observe_futures_account(
    reader: FuturesAccountReader, now: datetime
) -> FuturesAccountObservation:
    """Run the four reads; each failure is recorded and leaves its value unknown."""
    failures: list[str] = []
    balance = await _attempt(reader.balance_summary, failures)
    positions = await _attempt(reader.positions, failures)
    setting = await _attempt(reader.intraday_margin_setting, failures)
    window = await _attempt(reader.current_margin_window, failures)
    return FuturesAccountObservation(
        observed_at=now,
        enablement=(FuturesEnablement.UNKNOWN if balance is None else FuturesEnablement.ENABLED),
        balance=balance,
        positions=positions,
        intraday_margin_setting=setting,
        margin_window=window,
        read_failures=tuple(failures),
    )


async def _attempt[T](read: Callable[[], Awaitable[T]], failures: list[str]) -> T | None:
    """Run one read; record ``operation:reason`` and return ``None`` on failure."""
    try:
        return await read()
    except FuturesAccountReadError as error:
        failures.append(f"{error.operation}:{error.reason}")
        return None


async def run_futures_mirror(
    stop_requested: asyncio.Event,
    *,
    reader: Callable[[], FuturesAccountReader | None],
    store: FuturesAccountSnapshotStore,
    interval_seconds: float = FUTURES_MIRROR_INTERVAL_SECONDS,
    now_factory: Callable[[], datetime] | None = None,
) -> None:
    """Mirror until stopped; with no reader (demo mode) record nothing.

    ``reader`` is called every cycle so a credential reload takes effect without restart.
    """
    clock = now_factory or (lambda: datetime.now(UTC))
    while not stop_requested.is_set():
        current = reader()
        if current is not None:
            observation = await observe_futures_account(current, clock())
            try:
                await store.record(observation)
            except FuturesAccountStoreUnavailableError:
                _logger.warning("futures_mirror_store_unavailable")
            else:
                _logger.info(
                    "futures_mirror_observed enablement=%s positions=%s failures=%d",
                    observation.enablement.value,
                    "unknown" if observation.positions is None else len(observation.positions),
                    len(observation.read_failures),
                )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)
