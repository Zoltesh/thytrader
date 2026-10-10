"""CFM futures account mirror loop for the worker process (ADR 0127, slice P0-5).

Every 60 seconds the mirror reads the CFM balance summary, positions, intraday margin
setting and current margin window through the GET-only adapter, then the spot account
listing for the USDC and USD balances (USDC is CFM collateral), and appends one snapshot.
Each read fails independently and is recorded as failure evidence; a failed read is
unknown, never an empty or zero value. Enablement is ``enabled`` only when the balance
summary parsed. The reads are sequential, not one atomic venue snapshot. Nothing here can
place, cancel or close an order, sweep funds or change a margin setting.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.exchanges.coinbase import CoinbasePaginationError
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountReadError,
    FuturesAccountStoreUnavailableError,
    FuturesEnablement,
    SpotCollateralBalances,
    spot_collateral_from,
)
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.exchanges.protocols import FuturesAccountReader, SpotBalanceReader

_logger = logging.getLogger(__name__)
FUTURES_MIRROR_INTERVAL_SECONDS = 60.0


_SPOT_OPERATION = "spot_balances"


async def observe_futures_account(
    reader: FuturesAccountReader, now: datetime, spot: SpotBalanceReader | None = None
) -> FuturesAccountObservation:
    """Run the four CFM reads, then the spot listing; each failure is recorded, never guessed."""
    failures: list[str] = []
    balance = await _attempt(reader.balance_summary, failures)
    positions = await _attempt(reader.positions, failures)
    setting = await _attempt(reader.intraday_margin_setting, failures)
    window = await _attempt(reader.current_margin_window, failures)
    spot_balances = None if spot is None else await _attempt_spot(spot, failures)
    return FuturesAccountObservation(
        observed_at=now,
        enablement=(FuturesEnablement.UNKNOWN if balance is None else FuturesEnablement.ENABLED),
        balance=balance,
        positions=positions,
        intraday_margin_setting=setting,
        margin_window=window,
        read_failures=tuple(failures),
        spot_balances=spot_balances,
    )


async def _attempt[T](read: Callable[[], Awaitable[T]], failures: list[str]) -> T | None:
    """Run one read; record ``operation:reason`` and return ``None`` on failure."""
    try:
        return await read()
    except FuturesAccountReadError as error:
        failures.append(f"{error.operation}:{error.reason}")
        return None


async def _attempt_spot(
    spot: SpotBalanceReader, failures: list[str]
) -> SpotCollateralBalances | None:
    """Read the complete spot listing; record ``spot_balances:<reason>`` on failure."""
    try:
        return spot_collateral_from(await spot.list_balances())
    except ExchangeReadError as error:
        failures.append(f"{_SPOT_OPERATION}:{_spot_reason(error)}")
    except CoinbasePaginationError:
        failures.append(f"{_SPOT_OPERATION}:pagination")
    return None


def _spot_reason(error: ExchangeReadError) -> str:
    """Short redacted reason token, shaped like the CFM tokens (``http_503``)."""
    failure = error.failure
    if failure.kind is ExchangeReadFailureKind.HTTP and failure.http_status is not None:
        return f"http_{failure.http_status}"
    return failure.kind.value


async def run_futures_mirror(
    stop_requested: asyncio.Event,
    *,
    reader: Callable[[], FuturesAccountReader | None],
    store: FuturesAccountSnapshotStore,
    spot_reader: Callable[[], SpotBalanceReader | None] | None = None,
    interval_seconds: float = FUTURES_MIRROR_INTERVAL_SECONDS,
    now_factory: Callable[[], datetime] | None = None,
) -> None:
    """Mirror until stopped; with no reader (demo mode) record nothing.

    ``reader`` and ``spot_reader`` are called every cycle so a credential reload takes
    effect without restart.
    """
    clock = now_factory or (lambda: datetime.now(UTC))
    while not stop_requested.is_set():
        current = reader()
        if current is not None:
            spot = None if spot_reader is None else spot_reader()
            observation = await observe_futures_account(current, clock(), spot)
            try:
                await store.record(observation)
            except FuturesAccountStoreUnavailableError:
                _logger.warning("futures_mirror_store_unavailable")
            else:
                _logger.info(
                    "futures_mirror_observed enablement=%s positions=%s spot=%s failures=%d",
                    observation.enablement.value,
                    "unknown" if observation.positions is None else len(observation.positions),
                    "unknown" if observation.spot_balances is None else "read",
                    len(observation.read_failures),
                )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)
