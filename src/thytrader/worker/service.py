"""Core lifecycle for the continuously running ThyTrader worker.

When database persistence is configured, the worker fetches portfolio snapshots
on a schedule and records them automatically — no manual refresh required.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.persistence.portfolio_history import PortfolioHistoryUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

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
    audit_store: AuditEventStore | None = None,
    on_started: Callable[[], None] | None = None,
) -> None:
    """Run until graceful shutdown, recording scheduled portfolio snapshots."""
    runtime.ready = True
    if on_started is not None:
        on_started()
    interval = runtime.settings.snapshot_interval_seconds

    await _record_audit_event(
        audit_store,
        category=AuditEventCategory.CONNECTION,
        action="worker_started",
        outcome=AuditEventOutcome.INFO,
        detail="Portfolio worker started successfully",
    )

    if history_store is not None:
        await _take_snapshot(portfolio_service, history_store, audit_store)

    try:
        while not stop_requested.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=interval)
            if stop_requested.is_set():
                break
            if history_store is not None:
                await _take_snapshot(portfolio_service, history_store, audit_store)
    finally:
        runtime.ready = False
        await _record_audit_event(
            audit_store,
            category=AuditEventCategory.CONNECTION,
            action="worker_stopped",
            outcome=AuditEventOutcome.INFO,
            detail="Portfolio worker stopped",
        )


async def _record_audit_event(
    audit_store: AuditEventStore | None,
    *,
    category: AuditEventCategory,
    action: str,
    outcome: AuditEventOutcome,
    detail: str = "",
    provider: str | None = None,
    product_id: str | None = None,
) -> None:
    """Helper to safely record an audit event without propagating persistence errors."""
    if audit_store is None:
        return
    try:
        event = AuditEvent(
            occurred_at=datetime.now(UTC),
            category=category,
            action=action,
            outcome=outcome,
            detail=detail,
            provider=provider,
            product_id=product_id,
        )
        await audit_store.append(event)
    except Exception:
        _logger.exception("Failed to record worker audit event")


async def _take_snapshot(
    portfolio_service: PortfolioFetcher,
    history_store: PortfolioHistoryStore,
    audit_store: AuditEventStore | None = None,
) -> None:
    """Fetch a portfolio and persist it, logging redacted errors on failure."""
    try:
        portfolio = await portfolio_service.get_portfolio()
    except Exception as exc:
        _logger.exception("Worker portfolio fetch failed")
        await _record_audit_event(
            audit_store,
            category=AuditEventCategory.WORKER_ERROR,
            action="portfolio_fetch_failed",
            outcome=AuditEventOutcome.FAILURE,
            detail=f"Portfolio fetch failed: {exc.__class__.__name__}",
        )
        return

    if portfolio.demo:
        _logger.debug("Skipping snapshot for demo portfolio")
        return

    try:
        await history_store.record(portfolio)
        _logger.info("Portfolio snapshot recorded")
        await _record_audit_event(
            audit_store,
            category=AuditEventCategory.SNAPSHOT,
            action="portfolio_snapshot_recorded",
            outcome=AuditEventOutcome.SUCCESS,
            detail=f"Snapshot recorded total_value={portfolio.total_value.amount} USD",
            provider=portfolio.connection.provider,
        )
    except PortfolioHistoryUnavailableError:
        pass
    except Exception as exc:
        _logger.exception("Worker snapshot persistence failed")
        await _record_audit_event(
            audit_store,
            category=AuditEventCategory.WORKER_ERROR,
            action="snapshot_persistence_failed",
            outcome=AuditEventOutcome.FAILURE,
            detail=f"Snapshot persistence failed: {exc.__class__.__name__}",
            provider=portfolio.connection.provider,
        )
