"""Main loop of one research worker process: claim, run, heartbeat, recycle (ADR 0092).

A process runs one job at a time. Between jobs it checks the recycle rule (job count,
RSS growth) and exits cleanly so the supervisor starts a fresh interpreter. On SIGTERM
it hands a running job back to the queue (``release``) and exits at once, without
waiting for a simulation thread that cannot be interrupted.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import logging
import os
import signal
from typing import TYPE_CHECKING, Final

from thytrader.persistence.database import create_engine, dispose, ping
from thytrader.persistence.postgres_research_queue import ResearchQueueUnavailableError
from thytrader.research.worker_pool import RESEARCH_QUEUES, ResearchQueueName
from thytrader.research_worker.executor import ResearchJobExecutor, build_research_services
from thytrader.research_worker.lease import LeaseHeartbeat, WorkerStatus
from thytrader.research_worker.memory import process_rss_bytes
from thytrader.research_worker.spec import WorkerExit, recycle_reason

if TYPE_CHECKING:
    from thytrader.persistence.postgres_research_queue import PostgresResearchQueue
    from thytrader.research.worker_pool import ClaimedResearchJob
    from thytrader.research_worker.spec import WorkerProcessSpec

_logger = logging.getLogger(__name__)
_REVERSED_QUEUES: Final[tuple[ResearchQueueName, ...]] = tuple(reversed(RESEARCH_QUEUES))


async def run_worker_process(spec: WorkerProcessSpec) -> WorkerExit:
    """Run one worker process until it is stopped or decides to recycle."""
    engine = create_engine(spec.database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    status = WorkerStatus(
        slot=spec.slot, pool_size=spec.pool_size, pid=os.getpid(), started_at=datetime.now(UTC)
    )
    heartbeat = LeaseHeartbeat(spec, status)
    try:
        await ping(engine)
        services = build_research_services(engine, spec.dataset_root)
        await _recover_predecessor(services.queue, spec)
        heartbeat.start()
        executor = ResearchJobExecutor(services=services, owner=spec.owner)
        return await _work(spec, services.queue, executor, status, stop)
    finally:
        status.stopping()
        heartbeat.stop()
        await dispose(engine)


async def _recover_predecessor(queue: PostgresResearchQueue, spec: WorkerProcessSpec) -> None:
    """Re-queue what this slot's previous (now dead) process left running."""
    counts = await queue.requeue_lineage(spec.lineage, max_attempts=spec.max_attempts)
    if counts.total:
        _logger.info(
            "research_worker_predecessor_recovered slot=%s requeued=%s cancelled=%s failed=%s",
            spec.slot,
            counts.requeued,
            counts.cancelled,
            counts.failed,
        )
    await queue.prune_slots(spec.pool_size)


async def _work(
    spec: WorkerProcessSpec,
    queue: PostgresResearchQueue,
    executor: ResearchJobExecutor,
    status: WorkerStatus,
    stop: asyncio.Event,
) -> WorkerExit:
    """Claim and run jobs until stopped or recycled."""
    warm_rss: int | None = None
    flip = False
    status.idle()
    while not stop.is_set():
        claimed = await _claim(queue, spec, order=_REVERSED_QUEUES if flip else RESEARCH_QUEUES)
        flip = not flip
        if claimed is None:
            await _sleep_unless_stopped(stop, spec.poll_seconds)
            continue
        status.running(claimed)
        try:
            finished = await _run_or_stop(executor, claimed, stop)
        except Exception:
            # An unexpected escape (storage outage while recording the outcome): give
            # the row back now, attempt still counted, instead of waiting for expiry.
            _logger.exception("research_job_crashed job_id=%s slot=%s", claimed.job_id, spec.slot)
            await queue.requeue_lineage(spec.owner, max_attempts=spec.max_attempts)
            finished = True
        if not finished:
            await queue.release(spec.owner)
            _logger.info("research_worker_released job_id=%s slot=%s", claimed.job_id, spec.slot)
            return WorkerExit.STOPPED_MID_JOB
        status.finished()
        rss = process_rss_bytes()
        warm_rss = rss if warm_rss is None else warm_rss
        reason = recycle_reason(
            spec,
            jobs_completed=status.jobs_completed,
            rss_bytes=rss,
            warm_rss_bytes=warm_rss,
        )
        if reason is not None:
            _logger.info(
                "research_worker_recycling slot=%s reason=%s jobs=%s rss_bytes=%s",
                spec.slot,
                reason.value,
                status.jobs_completed,
                rss,
            )
            return WorkerExit.RECYCLE
    return WorkerExit.STOPPED


async def _claim(
    queue: PostgresResearchQueue,
    spec: WorkerProcessSpec,
    *,
    order: tuple[ResearchQueueName, ...],
) -> ClaimedResearchJob | None:
    """Claim the next job, alternating which queue goes first so neither starves."""
    try:
        return await queue.claim(spec.owner, lease_seconds=spec.lease_seconds, order=order)
    except ResearchQueueUnavailableError:
        _logger.warning("research_worker_claim_failed slot=%s", spec.slot)
        return None


async def _run_or_stop(
    executor: ResearchJobExecutor,
    claimed: ClaimedResearchJob,
    stop: asyncio.Event,
) -> bool:
    """Run the job; return False when a stop arrived first (the job is abandoned)."""
    job = asyncio.create_task(executor.execute(claimed), name=f"research-job-{claimed.job_id}")
    stopped = asyncio.create_task(stop.wait(), name="research-worker-stop")
    done, _pending = await asyncio.wait({job, stopped}, return_when=asyncio.FIRST_COMPLETED)
    if job in done:
        stopped.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopped
        job.result()
        return True
    job.cancel()
    return False


async def _sleep_unless_stopped(stop: asyncio.Event, seconds: float) -> None:
    """Wait ``seconds`` for new work, returning early on stop."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
