"""Core lifecycle for the continuously running ThyTrader worker.

When database persistence is configured, the worker fetches portfolio snapshots
on a schedule and records them automatically — no manual refresh required.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.persistence.portfolio_history import PortfolioHistoryUnavailableError

if TYPE_CHECKING:
    from thytrader.persistence.portfolio_history import PortfolioHistoryStore
    from thytrader.portfolio.models import Portfolio
    from thytrader.runtime import RuntimeState

_logger = logging.getLogger(__name__)


@runtime_checkable
class PortfolioFetcher(Protocol):
    """Read-only contract for fetching a portfolio snapshot."""

    async def get_portfolio(self) -> Portfolio:
        """Return the current portfolio for snapshot recording."""
        ...


async def run_worker(
    runtime: RuntimeState,
    stop_requested: asyncio.Event,
    *,
    portfolio_service: PortfolioFetcher,
    history_store: PortfolioHistoryStore | None = None,
) -> None:
    """Run until graceful shutdown, recording scheduled portfolio snapshots."""
    runtime.ready = True
    interval = runtime.settings.snapshot_interval_seconds

    if history_store is not None:
        await _take_snapshot(portfolio_service, history_store)

    try:
        while not stop_requested.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=interval)
            if stop_requested.is_set():
                break
            if history_store is not None:
                await _take_snapshot(portfolio_service, history_store)
    finally:
        runtime.ready = False


async def _take_snapshot(
    portfolio_service: PortfolioFetcher,
    history_store: PortfolioHistoryStore,
) -> None:
    """Fetch a portfolio and persist it, logging redacted errors on failure."""
    try:
        portfolio = await portfolio_service.get_portfolio()
    except Exception:
        _logger.exception("Worker portfolio fetch failed")
        return

    if portfolio.demo:
        _logger.debug("Skipping snapshot for demo portfolio")
        return

    try:
        await history_store.record(portfolio)
        _logger.info("Portfolio snapshot recorded")
    except PortfolioHistoryUnavailableError:
        pass
    except Exception:
        _logger.exception("Worker snapshot persistence failed")
