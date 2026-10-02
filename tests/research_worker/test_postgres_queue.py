"""Live PostgreSQL coverage for research job claims, leases, re-queues, and fencing (ADR 0092).

Needs ``THYTRADER_TEST_DATABASE_URL``; every test runs in its own database cloned from
one migrated template.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.research_worker.support import (
    TEST_DATABASE_URL,
    cloned_database,
    migrated_template,
)
from thytrader.backtest.submission import BacktestSubmissionRequest
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_research_jobs import PostgresResearchJobStore
from thytrader.persistence.postgres_research_queue import PostgresResearchQueue
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.research.jobs import (
    ResearchJobErrorCode,
    ResearchJobLeaseLostError,
    ResearchJobStatus,
)
from thytrader.research.worker_pool import ResearchWorkerSlot
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


@pytest.fixture(scope="module")
def template() -> Iterator[str]:
    """Migrate one template database for the whole module."""
    with migrated_template() as name:
        yield name


@pytest.fixture
def database(template: str) -> Iterator[str]:
    """Clone a fresh database for one test."""
    with cloned_database(template) as url:
        yield url


def _request(fingerprint: str, slippage: int = 5) -> BacktestSubmissionRequest:
    """Return one queued backtest payload for ``fingerprint``."""
    return BacktestSubmissionRequest(
        strategy_fingerprint=fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        evaluation_start=datetime(2026, 7, 10, tzinfo=UTC),
        evaluation_end=datetime(2026, 7, 12, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps=str(slippage),
    )


def _run(url: str, scenario: Callable[[AsyncEngine, UUID, str], Awaitable[None]]) -> None:
    """Create a strategy, then run ``scenario(engine, strategy_id, fingerprint)``."""

    async def body() -> None:
        engine = create_engine(SecretStr(url))
        try:
            record = await create_strategy_from_definition(
                PostgresStrategyStore(engine), create_template_strategy()
            )
            fingerprint = record.current_fingerprint
            assert fingerprint is not None
            await scenario(engine, record.strategy_id, fingerprint)
        finally:
            await dispose(engine)

    asyncio.run(body())


async def _queue_jobs(
    engine: AsyncEngine, strategy_id: UUID, fingerprint: str, count: int
) -> list[UUID]:
    """Queue ``count`` backtests and return their ids oldest first."""
    store = PostgresResearchJobStore(engine)
    ids: list[UUID] = []
    for index in range(count):
        record = await store.create_backtest(
            _request(fingerprint, 5 + index), strategy_id=strategy_id
        )
        ids.append(record.job_id)
    return ids


async def _expire_leases(engine: AsyncEngine) -> None:
    """Move every running lease into the past (a dead worker)."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE research_jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE status = 'running'"
            )
        )


def test_concurrent_claims_never_share_a_job(database: str) -> None:
    """Five workers racing for three jobs claim each job exactly once, oldest first."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        ids = await _queue_jobs(engine, strategy_id, fingerprint, 3)
        queue = PostgresResearchQueue(engine)
        claims = await asyncio.gather(
            *(queue.claim(f"host/1/{slot}/n{slot}", lease_seconds=30) for slot in range(5))
        )
        claimed = [claim for claim in claims if claim is not None]
        assert sorted(claim.job_id for claim in claimed) == sorted(ids)
        assert all(claim.attempts == 1 and claim.kind == "backtest" for claim in claimed)
        store = PostgresResearchJobStore(engine)
        for job_id in ids:
            record = await store.get(job_id)
            assert record is not None
            assert record.status is ResearchJobStatus.RUNNING
            assert record.attempts == 1
        assert await queue.claim("host/1/9/late", lease_seconds=30) is None

    _run(database, scenario)


def test_claims_take_the_oldest_queued_job_first(database: str) -> None:
    """One worker claims jobs in creation order."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        ids = await _queue_jobs(engine, strategy_id, fingerprint, 3)
        queue = PostgresResearchQueue(engine)
        order = [await queue.claim("host/1/0/a", lease_seconds=30) for _ in ids]
        assert [claim.job_id for claim in order if claim is not None] == ids

    _run(database, scenario)


def test_expired_lease_is_requeued_then_failed_at_the_attempt_limit(database: str) -> None:
    """A dead worker's job is re-queued; after max attempts it fails as worker_lost."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        (job_id,) = await _queue_jobs(engine, strategy_id, fingerprint, 1)
        queue = PostgresResearchQueue(engine)
        store = PostgresResearchJobStore(engine)
        assert await queue.claim("host/1/0/first", lease_seconds=30) is not None
        assert (await queue.requeue_expired(max_attempts=2)).total == 0
        await _expire_leases(engine)
        assert (await queue.requeue_expired(max_attempts=2)).requeued == 1
        requeued = await store.get(job_id)
        assert requeued is not None
        assert requeued.status is ResearchJobStatus.QUEUED
        second = await queue.claim("host/1/0/second", lease_seconds=30)
        assert second is not None
        assert second.attempts == 2
        await _expire_leases(engine)
        assert (await queue.requeue_expired(max_attempts=2)).failed == 1
        failed = await store.get(job_id)
        assert failed is not None
        assert failed.status is ResearchJobStatus.FAILED
        assert failed.error_code is ResearchJobErrorCode.RESEARCH_WORKER_LOST
        assert failed.attempts == 2

    _run(database, scenario)


def test_orphan_with_cancel_requested_ends_cancelled(database: str) -> None:
    """Cancelling a job whose worker died must not resurrect it."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        (job_id,) = await _queue_jobs(engine, strategy_id, fingerprint, 1)
        queue = PostgresResearchQueue(engine)
        store = PostgresResearchJobStore(engine)
        assert await queue.claim("host/1/0/a", lease_seconds=30) is not None
        await store.cancel(job_id)
        await _expire_leases(engine)
        assert (await queue.requeue_expired(max_attempts=3)).cancelled == 1
        record = await store.get(job_id)
        assert record is not None
        assert record.status is ResearchJobStatus.CANCELLED

    _run(database, scenario)


def test_legacy_running_row_without_a_lease_is_requeued(database: str) -> None:
    """A row the pre-0057 API was running (no lease) is swept back to the queue."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        (job_id,) = await _queue_jobs(engine, strategy_id, fingerprint, 1)
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE research_jobs SET status = 'running'"))
        counts = await PostgresResearchQueue(engine).requeue_expired(max_attempts=3)
        assert counts.requeued == 1
        record = await PostgresResearchJobStore(engine).get(job_id)
        assert record is not None
        assert record.status is ResearchJobStatus.QUEUED

    _run(database, scenario)


def test_renew_keeps_a_live_lease_and_lineage_recovery_frees_only_the_dead_slot(
    database: str,
) -> None:
    """Renewal protects a live job; a replacement process frees only its own slot."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        first, second = await _queue_jobs(engine, strategy_id, fingerprint, 2)
        queue = PostgresResearchQueue(engine)
        assert await queue.claim("host/7/0/dead", lease_seconds=30) is not None
        assert await queue.claim("host/7/1/live", lease_seconds=30) is not None
        await _expire_leases(engine)
        assert await queue.renew("host/7/1/live", lease_seconds=30) == 1
        recovered = await queue.requeue_lineage("host/7/0/", max_attempts=3)
        assert recovered.requeued == 1
        store = PostgresResearchJobStore(engine)
        dead = await store.get(first)
        live = await store.get(second)
        assert dead is not None and dead.status is ResearchJobStatus.QUEUED
        assert live is not None and live.status is ResearchJobStatus.RUNNING
        assert (await queue.requeue_expired(max_attempts=3)).total == 0

    _run(database, scenario)


def test_release_hands_the_job_back_without_spending_the_attempt(database: str) -> None:
    """A gracefully stopping worker re-queues its job and the attempt is not counted."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        (job_id,) = await _queue_jobs(engine, strategy_id, fingerprint, 1)
        queue = PostgresResearchQueue(engine)
        assert await queue.claim("host/1/0/a", lease_seconds=30) is not None
        assert await queue.release("host/1/0/a") == 1
        record = await PostgresResearchJobStore(engine).get(job_id)
        assert record is not None
        assert record.status is ResearchJobStatus.QUEUED
        assert record.attempts == 0

    _run(database, scenario)


def test_fenced_writes_require_the_current_lease(database: str) -> None:
    """A worker that lost its lease cannot write progress or an outcome."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        (job_id,) = await _queue_jobs(engine, strategy_id, fingerprint, 1)
        queue = PostgresResearchQueue(engine)
        store = PostgresResearchJobStore(engine)
        assert await queue.claim("host/1/0/owner", lease_seconds=30) is not None
        with pytest.raises(ResearchJobLeaseLostError):
            await store.leased("host/1/0/stale").update_progress(
                job_id, progress_current=1, progress_total=1
            )
        owner = store.leased("host/1/0/owner")
        await owner.update_progress(job_id, progress_current=1, progress_total=1)
        done = await owner.mark_completed_backtest(
            job_id, run_fingerprint="sha256:" + "c" * 64, result_fingerprint="sha256:" + "d" * 64
        )
        assert done.status is ResearchJobStatus.COMPLETED
        assert await queue.renew("host/1/0/owner", lease_seconds=30) == 0
        with pytest.raises(ResearchJobLeaseLostError):
            await owner.mark_failed(job_id, error_message="late")
        lease = await _lease_owner(engine, job_id)
        assert lease is None

    _run(database, scenario)


def test_snapshot_reports_queue_depth_and_worker_slots(database: str) -> None:
    """Health reads queued/running counts, the oldest queued time, and slot rows."""

    async def scenario(engine: AsyncEngine, strategy_id: UUID, fingerprint: str) -> None:
        ids = await _queue_jobs(engine, strategy_id, fingerprint, 3)
        queue = PostgresResearchQueue(engine)
        assert await queue.claim("host/1/0/a", lease_seconds=30) is not None
        now = datetime.now(UTC)
        await queue.record_slot(
            ResearchWorkerSlot(
                slot=0,
                pool_size=2,
                pid=4242,
                state="running",
                job_id=ids[0],
                job_kind="backtest",
                jobs_completed=3,
                rss_bytes=150 * 2**20,
                started_at=now,
                heartbeat_at=now,
            )
        )
        await queue.record_slot(
            ResearchWorkerSlot(
                slot=3,
                pool_size=4,
                pid=4343,
                state="idle",
                jobs_completed=0,
                started_at=now,
                heartbeat_at=now,
            )
        )
        assert await queue.prune_slots(2) == 1
        snapshot = await queue.snapshot()
        assert snapshot.research_jobs.queued == 2
        assert snapshot.research_jobs.running == 1
        assert snapshot.research_jobs.oldest_queued_at is not None
        assert snapshot.portfolio_backtests.queued == 0
        assert [slot.slot for slot in snapshot.workers] == [0]
        assert snapshot.workers[0].rss_bytes == 150 * 2**20
        assert snapshot.workers[0].job_id == ids[0]
        assert (
            await queue.claim("host/1/1/b", lease_seconds=30, order=("portfolio_backtest_jobs",))
            is None
        )

    _run(database, scenario)


async def _lease_owner(engine: AsyncEngine, job_id: UUID) -> object:
    """Return one job's lease owner column."""
    async with engine.connect() as connection:
        statement = text("SELECT lease_owner FROM research_jobs WHERE job_id = :job")
        return (await connection.execute(statement, {"job": job_id})).scalar()
