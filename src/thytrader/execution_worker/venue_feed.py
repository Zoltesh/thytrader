"""Restart the user-order feed whenever the execution venue credential generation changes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)
from thytrader.execution.ids import utc_now
from thytrader.execution_worker.user_feed import run_user_order_feed
from thytrader.execution_worker.venue import venue_transition_detail

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.audit_events import AuditEventStore
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.execution_worker.venue import ExecutionVenue

_logger = logging.getLogger(__name__)
_GENERATION_POLL_SECONDS = 1.0


class UserFeedRunner(Protocol):
    """Callable shape of ``run_user_order_feed`` so tests can substitute a fake."""

    async def __call__(
        self,
        stop_requested: asyncio.Event,
        *,
        enabled: bool,
        feed_store: UserOrderFeedStateStore,
        jwt_provider: Callable[[], str] | None = None,
        audit_store: AuditEventStore | None = None,
        wake_requested: asyncio.Event | None = None,
    ) -> None:
        """Run one feed generation until its stop event is set."""
        ...


async def run_venue_user_order_feed(
    stop_requested: asyncio.Event,
    *,
    venue_provider: Callable[[], ExecutionVenue],
    feed_store: UserOrderFeedStateStore,
    audit_store: AuditEventStore | None = None,
    wake_requested: asyncio.Event | None = None,
    runner: UserFeedRunner = run_user_order_feed,
    poll_seconds: float = _GENERATION_POLL_SECONDS,
) -> None:
    """Supervise one user-feed generation per venue generation until shutdown.

    Each generation gets its own stop event. When the credential generation
    changes, the old feed is stopped and awaited before a new one starts with
    the new JWT provider (or records DISABLED when credentials were cleared).
    """
    venue = venue_provider()
    await _audit_venue(audit_store, venue, action="execution_venue_bound")
    while not stop_requested.is_set():
        generation_stop = asyncio.Event()
        task = asyncio.create_task(
            runner(
                generation_stop,
                enabled=venue.live_enabled,
                feed_store=feed_store,
                jwt_provider=venue.jwt_provider,
                audit_store=audit_store,
                wake_requested=wake_requested,
            )
        )
        next_venue = await _wait_for_generation_change(
            stop_requested, task, venue_provider, venue, poll_seconds
        )
        generation_stop.set()
        await task
        if next_venue is None:
            return
        venue = next_venue
        await _audit_venue(audit_store, venue, action="execution_venue_reloaded")
        if wake_requested is not None:
            wake_requested.set()


async def _wait_for_generation_change(
    stop_requested: asyncio.Event,
    task: asyncio.Task[None],
    venue_provider: Callable[[], ExecutionVenue],
    venue: ExecutionVenue,
    poll_seconds: float,
) -> ExecutionVenue | None:
    """Return the next venue on a generation change, or None on shutdown.

    A feed task that exits on its own re-raises here so a crashed feed still
    stops the worker, exactly as the unsupervised ``asyncio.gather`` did.
    """
    while not stop_requested.is_set():
        if task.done():
            task.result()
            return None
        current = venue_provider()
        if current.generation != venue.generation:
            return current
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_requested.wait(), timeout=poll_seconds)
    return None


async def _audit_venue(
    audit_store: AuditEventStore | None,
    venue: ExecutionVenue,
    *,
    action: str,
) -> None:
    """Append a redacted venue-binding audit row; never block the feed on audit failure."""
    if audit_store is None:
        return
    try:
        await audit_store.append(
            AuditEvent(
                occurred_at=utc_now(),
                category=AuditEventCategory.RUNTIME,
                action=action,
                outcome=AuditEventOutcome.INFO,
                detail=venue_transition_detail(venue),
                provider=venue.source,
            )
        )
    except AuditEventUnavailableError:
        _logger.warning("execution_venue_audit_unavailable action=%s", action)
