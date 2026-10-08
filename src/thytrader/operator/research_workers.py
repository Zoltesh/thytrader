"""Turn a research worker pool snapshot into operator health (ADR 0092).

A worker is *live* when its slot row heartbeated within ``stale_after_seconds``. The
component is healthy only when every configured slot is live; queue depth and the
oldest queued age are reported either way so an agent can tell "queued behind busy
workers" from "queued with no worker to run it".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from thytrader.operator.health_models import (
    ResearchQueueReport,
    ResearchWorkerReport,
    ResearchWorkersPayload,
)
from thytrader.operator.models import ComponentReport, ReportStatus

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.research.worker_pool import (
        ResearchQueueDepth,
        ResearchWorkerPoolSnapshot,
        ResearchWorkerSlot,
    )

COMPONENT_NAME: Final = "research_worker"
MIN_STALE_AFTER_SECONDS: Final = 30.0


class ResearchQueueSnapshotReader(Protocol):
    """Read both research queues and every worker slot in one call."""

    async def snapshot(self) -> ResearchWorkerPoolSnapshot:
        """Return queue depth and worker slot rows."""
        ...


def stale_after_seconds(lease_seconds: float) -> float:
    """Treat a slot as dead after three missed heartbeats (at least 30 seconds)."""
    return max(MIN_STALE_AFTER_SECONDS, 3 * max(0.25, min(10.0, lease_seconds / 6)))


def research_worker_health(
    snapshot: ResearchWorkerPoolSnapshot,
    *,
    now: datetime,
    stale_after: float,
) -> tuple[ComponentReport, ResearchWorkersPayload]:
    """Build the ``research_worker`` component and the health payload block."""
    newest = max(snapshot.workers, key=lambda slot: slot.heartbeat_at, default=None)
    configured = None if newest is None else newest.pool_size
    workers = tuple(
        _worker(slot, now=now, stale_after=stale_after)
        for slot in snapshot.workers
        if configured is None or slot.slot < configured
    )
    research = _queue(snapshot.research_jobs, now)
    portfolio = _queue(snapshot.portfolio_backtests, now)
    payload = ResearchWorkersPayload(
        configured_workers=configured,
        live_workers=sum(1 for worker in workers if worker.live),
        queue=_combined(research, portfolio),
        research_jobs=research,
        portfolio_backtests=portfolio,
        workers=workers,
    )
    return _component(payload, newest, now), payload


def _component(
    payload: ResearchWorkersPayload,
    newest: ResearchWorkerSlot | None,
    now: datetime,
) -> ComponentReport:
    """Grade pool liveness and describe the queue in one line."""
    queue = payload.queue
    depth = f"{queue.queued} queued, {queue.running} running"
    if queue.oldest_queued_age_seconds is not None:
        depth += f"; oldest queued {int(queue.oldest_queued_age_seconds)}s ago"
    if newest is None or payload.configured_workers is None:
        return ComponentReport(
            name=COMPONENT_NAME,
            status=ReportStatus.DEGRADED,
            reason_code="RESEARCH_WORKER_MISSING",
            detail=f"No research worker has reported; queued research cannot run ({depth}).",
        )
    if payload.live_workers == 0:
        age = int((now - newest.heartbeat_at).total_seconds())
        return ComponentReport(
            name=COMPONENT_NAME,
            status=ReportStatus.DEGRADED,
            reason_code="RESEARCH_WORKER_STALE",
            detail=f"No research worker heartbeated in {age}s; queued research is stalled "
            f"({depth}).",
        )
    summary = f"{payload.live_workers} of {payload.configured_workers} research workers live"
    if payload.live_workers < payload.configured_workers:
        return ComponentReport(
            name=COMPONENT_NAME,
            status=ReportStatus.DEGRADED,
            reason_code="RESEARCH_WORKER_PARTIAL",
            detail=f"{summary}; {depth}.",
        )
    return ComponentReport(
        name=COMPONENT_NAME,
        status=ReportStatus.HEALTHY,
        reason_code="READY",
        detail=f"{summary}; {depth}.",
    )


def _worker(slot: ResearchWorkerSlot, *, now: datetime, stale_after: float) -> ResearchWorkerReport:
    """Report one slot with its heartbeat age and liveness."""
    age = max(0.0, (now - slot.heartbeat_at).total_seconds())
    return ResearchWorkerReport(
        slot=slot.slot,
        pid=slot.pid,
        state=slot.state,
        live=age <= stale_after,
        job_id=slot.job_id,
        job_kind=slot.job_kind,
        jobs_completed=slot.jobs_completed,
        rss_bytes=slot.rss_bytes,
        started_at=slot.started_at,
        heartbeat_at=slot.heartbeat_at,
        heartbeat_age_seconds=round(age, 3),
    )


def _queue(depth: ResearchQueueDepth, now: datetime) -> ResearchQueueReport:
    """Report one queue with the oldest queued job's age."""
    age = None
    if depth.oldest_queued_at is not None:
        age = round(max(0.0, (now - depth.oldest_queued_at).total_seconds()), 3)
    return ResearchQueueReport(
        queued=depth.queued,
        running=depth.running,
        oldest_queued_at=depth.oldest_queued_at,
        oldest_queued_age_seconds=age,
    )


def _combined(first: ResearchQueueReport, second: ResearchQueueReport) -> ResearchQueueReport:
    """Sum two queues; the oldest queued job is whichever waited longest."""
    oldest = [item for item in (first, second) if item.oldest_queued_age_seconds is not None]
    winner = max(oldest, key=lambda item: item.oldest_queued_age_seconds or 0.0, default=None)
    return ResearchQueueReport(
        queued=first.queued + second.queued,
        running=first.running + second.running,
        oldest_queued_at=None if winner is None else winner.oldest_queued_at,
        oldest_queued_age_seconds=None if winner is None else winner.oldest_queued_age_seconds,
    )
