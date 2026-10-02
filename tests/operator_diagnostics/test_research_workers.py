"""Operator health for the research worker pool (ADR 0092)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from thytrader.operator.models import ReportStatus
from thytrader.operator.research_workers import research_worker_health, stale_after_seconds
from thytrader.operator.status import recommend_next_action
from thytrader.research.worker_pool import (
    ResearchQueueDepth,
    ResearchWorkerPoolSnapshot,
    ResearchWorkerSlot,
)

_NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


def _slot(slot: int, *, age: float, pool_size: int = 2, rss_mb: int = 150) -> ResearchWorkerSlot:
    """Return one slot row that last heartbeated ``age`` seconds ago."""
    return ResearchWorkerSlot(
        slot=slot,
        pool_size=pool_size,
        pid=1000 + slot,
        state="running",
        job_id=uuid4(),
        job_kind="study",
        jobs_completed=4,
        rss_bytes=rss_mb * 2**20,
        started_at=_NOW - timedelta(hours=1),
        heartbeat_at=_NOW - timedelta(seconds=age),
    )


def _snapshot(*workers: ResearchWorkerSlot, queued: int = 3) -> ResearchWorkerPoolSnapshot:
    """Return a snapshot with ``queued`` research jobs (oldest 90 s) and one portfolio job."""
    return ResearchWorkerPoolSnapshot(
        research_jobs=ResearchQueueDepth(
            queued=queued, running=2, oldest_queued_at=_NOW - timedelta(seconds=90)
        ),
        portfolio_backtests=ResearchQueueDepth(
            queued=1, running=0, oldest_queued_at=_NOW - timedelta(seconds=30)
        ),
        workers=workers,
    )


def test_all_live_workers_are_ready_and_report_depth_and_rss() -> None:
    """Every configured slot heartbeating is healthy; depth sums both queues."""
    component, payload = research_worker_health(
        _snapshot(_slot(0, age=2), _slot(1, age=4)), now=_NOW, stale_after=30
    )
    assert component.status is ReportStatus.HEALTHY
    assert component.reason_code == "READY"
    assert "2 of 2 research workers live" in component.detail
    assert payload.configured_workers == 2
    assert payload.live_workers == 2
    assert payload.queue.queued == 4
    assert payload.queue.running == 2
    assert payload.queue.oldest_queued_age_seconds == 90.0
    assert payload.portfolio_backtests.oldest_queued_age_seconds == 30.0
    assert [worker.rss_bytes for worker in payload.workers] == [150 * 2**20, 150 * 2**20]
    assert all(worker.live for worker in payload.workers)


def test_a_silent_slot_degrades_the_pool() -> None:
    """One crash-looping slot leaves the other running but the pool is degraded."""
    component, payload = research_worker_health(
        _snapshot(_slot(0, age=2), _slot(1, age=300)), now=_NOW, stale_after=30
    )
    assert component.status is ReportStatus.DEGRADED
    assert component.reason_code == "RESEARCH_WORKER_PARTIAL"
    assert payload.live_workers == 1
    assert [worker.live for worker in payload.workers] == [True, False]
    assert "research-worker logs" in recommend_next_action((component,))


def test_no_live_worker_means_queued_research_is_stalled() -> None:
    """Rows exist but none heartbeated: queued work is not running."""
    component, payload = research_worker_health(
        _snapshot(_slot(0, age=120), _slot(1, age=130)), now=_NOW, stale_after=30
    )
    assert component.reason_code == "RESEARCH_WORKER_STALE"
    assert "120s" in component.detail
    assert payload.live_workers == 0
    assert "Restart the research-worker service" in recommend_next_action((component,))


def test_a_pool_that_never_reported_is_missing() -> None:
    """No slot row at all: the service never started."""
    component, payload = research_worker_health(_snapshot(queued=0), now=_NOW, stale_after=30)
    assert component.reason_code == "RESEARCH_WORKER_MISSING"
    assert payload.configured_workers is None
    assert payload.queue.queued == 1
    assert "make run" in recommend_next_action((component,))


def test_rows_beyond_a_shrunken_pool_are_ignored() -> None:
    """After the pool shrinks, a stale higher slot does not degrade the smaller pool."""
    newest = _slot(0, age=1, pool_size=1)
    old = _slot(1, age=500, pool_size=2)
    component, payload = research_worker_health(_snapshot(newest, old), now=_NOW, stale_after=30)
    assert component.reason_code == "READY"
    assert payload.configured_workers == 1
    assert [worker.slot for worker in payload.workers] == [0]


def test_staleness_tolerates_three_missed_heartbeats_and_at_least_thirty_seconds() -> None:
    """The default 60 s lease beats every 10 s, so 30 s of silence is stale."""
    assert stale_after_seconds(60) == 30.0
    assert stale_after_seconds(3) == 30.0
    assert stale_after_seconds(3600) == 30.0
