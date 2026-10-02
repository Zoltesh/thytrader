"""PostgreSQL repository for durable async research jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import socket
from typing import TYPE_CHECKING, Final, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.backtest.submission import BacktestSubmissionRequest
from thytrader.persistence.postgres_research_queue import (
    PostgresResearchQueue,
    ResearchQueueUnavailableError,
)
from thytrader.persistence.schema import research_jobs
from thytrader.research.jobs import (
    RESEARCH_JOB_EXPIRY_HOURS,
    ResearchJobErrorCode,
    ResearchJobKind,
    ResearchJobLeaseLostError,
    ResearchJobRecord,
    ResearchJobStatus,
    primary_strategy_fingerprint,
)
from thytrader.research.studies import ResearchStudyRequest

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine


_TERMINAL: Final = frozenset(
    {
        ResearchJobStatus.COMPLETED,
        ResearchJobStatus.FAILED,
        ResearchJobStatus.CANCELLED,
        ResearchJobStatus.EXPIRED,
    }
)
_HARNESS_LEASE_SECONDS: Final = 3_600.0
_HARNESS_MAX_ATTEMPTS: Final = 3


class ResearchJobUnavailableError(RuntimeError):
    """Signal that durable research-job storage is disabled or unreachable."""


class PostgresResearchJobStore:
    """Durable queue for backtests and composed studies.

    Bound to a ``lease_owner`` (see :meth:`leased`), every execution write also
    requires that the row is still running under that owner's lease and raises
    :class:`ResearchJobLeaseLostError` otherwise; a terminal write clears the lease.
    """

    def __init__(self, engine: AsyncEngine, *, lease_owner: str | None = None) -> None:
        """Bind the store to a managed async engine and an optional lease fence."""
        self._engine = engine
        self._lease_owner = lease_owner

    def leased(self, lease_owner: str) -> PostgresResearchJobStore:
        """Return a store whose execution writes are fenced by ``lease_owner``."""
        return PostgresResearchJobStore(self._engine, lease_owner=lease_owner)

    async def create_backtest(
        self, request: BacktestSubmissionRequest, *, strategy_id: UUID
    ) -> ResearchJobRecord:
        """Insert one queued backtest job and return its initial record."""
        return await self._create(
            ResearchJobKind.BACKTEST,
            request.model_dump_json(),
            strategy_id=strategy_id,
            strategy_fingerprint=request.strategy_fingerprint,
        )

    async def create_study(
        self, request: ResearchStudyRequest, *, strategy_id: UUID
    ) -> ResearchJobRecord:
        """Insert one queued study job and return its initial record."""
        return await self._create(
            ResearchJobKind.STUDY,
            request.model_dump_json(),
            strategy_id=strategy_id,
            strategy_fingerprint=primary_strategy_fingerprint(request),
        )

    async def list_for_strategy(
        self, strategy_id: UUID, *, limit: int
    ) -> tuple[ResearchJobRecord, ...]:
        """Return the strategy's newest jobs first (indexed by strategy and time)."""
        statement = (
            select(research_jobs)
            .where(research_jobs.c.strategy_id == str(strategy_id))
            .order_by(research_jobs.c.created_at.desc(), research_jobs.c.job_id.asc())
            .limit(limit)
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        return tuple(_record_from_row(row) for row in rows)

    async def _create(
        self,
        kind: ResearchJobKind,
        payload: str,
        *,
        strategy_id: UUID,
        strategy_fingerprint: str,
    ) -> ResearchJobRecord:
        """Insert one queued job row."""
        now = datetime.now(UTC)
        job_id = uuid4()
        statement = insert(research_jobs).values(
            job_id=job_id,
            kind=kind.value,
            status=ResearchJobStatus.QUEUED.value,
            strategy_id=str(strategy_id),
            strategy_fingerprint=strategy_fingerprint,
            payload=payload,
            progress_current=0,
            progress_total=1,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=RESEARCH_JOB_EXPIRY_HOURS),
            cancel_requested=False,
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        record = await self.get(job_id)
        if record is None:
            raise ResearchJobUnavailableError("Research jobs are unavailable.")
        return record

    async def get(self, job_id: UUID) -> ResearchJobRecord | None:
        """Return one job record when it exists."""
        statement = select(research_jobs).where(research_jobs.c.job_id == job_id)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        if row is None:
            return None
        return _record_from_row(row)

    async def claim_next(self) -> tuple[UUID, ResearchJobKind, str] | None:
        """Claim the oldest queued job for the in-process harness under a long lease.

        Production workers claim through :class:`PostgresResearchQueue` instead; this
        exists so the in-process test harness can drive a PostgreSQL store.
        """
        owner = f"in-process/{socket.gethostname()[:48]}/{os.getpid()}"
        try:
            claimed = await PostgresResearchQueue(self._engine).claim(
                owner, lease_seconds=_HARNESS_LEASE_SECONDS, order=("research_jobs",)
            )
        except ResearchQueueUnavailableError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        if claimed is None:
            return None
        kind = ResearchJobKind(claimed.kind)
        return claimed.job_id, kind, await self._payload(claimed.job_id)

    async def mark_running(self, job_id: UUID) -> ResearchJobRecord:
        """Transition one job to running."""
        return await self._replace(job_id, status=ResearchJobStatus.RUNNING)

    async def update_progress(
        self,
        job_id: UUID,
        *,
        progress_current: int,
        progress_total: int,
    ) -> ResearchJobRecord:
        """Persist bounded progress for one running job."""
        return await self._replace(
            job_id,
            status=ResearchJobStatus.RUNNING,
            progress_current=progress_current,
            progress_total=progress_total,
        )

    async def mark_completed_backtest(
        self,
        job_id: UUID,
        *,
        run_fingerprint: str,
        result_fingerprint: str,
    ) -> ResearchJobRecord:
        """Persist successful backtest completion fingerprints."""
        return await self._replace(
            job_id,
            status=ResearchJobStatus.COMPLETED,
            run_fingerprint=run_fingerprint,
            result_fingerprint=result_fingerprint,
        )

    async def mark_completed_study(
        self,
        job_id: UUID,
        *,
        study_fingerprint: str,
        plan_fingerprint: str,
    ) -> ResearchJobRecord:
        """Persist successful study completion fingerprints."""
        return await self._replace(
            job_id,
            status=ResearchJobStatus.COMPLETED,
            study_fingerprint=study_fingerprint,
            plan_fingerprint=plan_fingerprint,
        )

    async def mark_failed(
        self,
        job_id: UUID,
        *,
        error_message: str,
        failed_phase: str | None = None,
        failed_detail: str | None = None,
        error_code: ResearchJobErrorCode | None = None,
    ) -> ResearchJobRecord:
        """Persist a caller-visible or redacted failure with optional detail and code."""
        return await self._replace(
            job_id,
            status=ResearchJobStatus.FAILED,
            error_message=error_message[:256],
            error_code=error_code,
            failed_phase=failed_phase[:32] if failed_phase is not None else None,
            failed_detail=failed_detail[:500] if failed_detail is not None else None,
        )

    async def mark_cancelled(self, job_id: UUID) -> ResearchJobRecord:
        """Persist one cancelled job."""
        return await self._replace(job_id, status=ResearchJobStatus.CANCELLED)

    async def cancel(self, job_id: UUID) -> ResearchJobRecord:
        """Cancel one queued or running job."""
        current = await self.get(job_id)
        if current is None:
            message = f"Research job {job_id} was not found."
            raise KeyError(message)
        if current.status is ResearchJobStatus.COMPLETED:
            message = "Completed research jobs cannot be cancelled."
            raise ValueError(message)
        if current.status is ResearchJobStatus.RUNNING:
            return await self._replace(
                job_id,
                status=current.status,
                cancel_requested=True,
            )
        return await self._replace(job_id, status=ResearchJobStatus.CANCELLED)

    async def expire_stale(self) -> int:
        """Mark overdue queued or running jobs expired."""
        now = datetime.now(UTC)
        statement = (
            update(research_jobs)
            .where(
                research_jobs.c.expires_at <= now,
                research_jobs.c.status.in_(
                    [ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value]
                ),
            )
            .values(
                status=ResearchJobStatus.EXPIRED.value,
                updated_at=now,
                error_message="Research job expired.",
            )
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        return int(result.rowcount or 0)

    async def recover_interrupted(self) -> int:
        """Requeue running jobs whose lease expired (never a live worker's job).

        Before ADR 0092 this re-queued every running row at API startup. With a worker
        pool that would steal live jobs, so only rows with an expired or missing lease
        are swept.
        """
        try:
            counts = await PostgresResearchQueue(self._engine).requeue_expired(
                max_attempts=_HARNESS_MAX_ATTEMPTS, queues=("research_jobs",)
            )
        except ResearchQueueUnavailableError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        return counts.total

    async def load_backtest_request(self, job_id: UUID) -> BacktestSubmissionRequest:
        """Load the queued backtest payload for one job."""
        payload = await self._payload(job_id)
        return BacktestSubmissionRequest.model_validate_json(payload)

    async def load_study_request(self, job_id: UUID) -> ResearchStudyRequest:
        """Load the queued study payload for one job."""
        payload = await self._payload(job_id)
        return ResearchStudyRequest.model_validate_json(payload)

    async def is_cancel_requested(self, job_id: UUID) -> bool:
        """Return whether cancellation was requested for one job."""
        statement = select(research_jobs.c.cancel_requested).where(research_jobs.c.job_id == job_id)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        if row is None:
            return False
        return bool(row[0])

    async def _payload(self, job_id: UUID) -> str:
        """Return the stored JSON payload for one job."""
        statement = select(research_jobs.c.payload).where(research_jobs.c.job_id == job_id)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        if row is None:
            message = f"Research job {job_id} was not found."
            raise KeyError(message)
        return cast("str", row[0])

    async def _replace(
        self,
        job_id: UUID,
        *,
        status: ResearchJobStatus,
        error_message: str | None = None,
        error_code: ResearchJobErrorCode | None = None,
        failed_phase: str | None = None,
        failed_detail: str | None = None,
        run_fingerprint: str | None = None,
        result_fingerprint: str | None = None,
        study_fingerprint: str | None = None,
        plan_fingerprint: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        cancel_requested: bool | None = None,
    ) -> ResearchJobRecord:
        """Update one job row, fenced by this store's lease owner when it has one."""
        values = _replacement_values(
            status=status,
            error_message=error_message,
            error_code=error_code,
            failed_phase=failed_phase,
            failed_detail=failed_detail,
            run_fingerprint=run_fingerprint,
            result_fingerprint=result_fingerprint,
            study_fingerprint=study_fingerprint,
            plan_fingerprint=plan_fingerprint,
            progress_current=progress_current,
            progress_total=progress_total,
            cancel_requested=cancel_requested,
        )
        statement = update(research_jobs).where(research_jobs.c.job_id == job_id)
        if self._lease_owner is not None:
            statement = statement.where(
                research_jobs.c.lease_owner == self._lease_owner,
                research_jobs.c.status == ResearchJobStatus.RUNNING.value,
            )
            if status in _TERMINAL:
                values.update(lease_owner=None, lease_expires_at=None)
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement.values(**values))
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        if self._lease_owner is not None and not result.rowcount:
            message = f"Research job {job_id} is no longer leased to this worker."
            raise ResearchJobLeaseLostError(message)
        record = await self.get(job_id)
        if record is None:
            raise ResearchJobUnavailableError("Research jobs are unavailable.")
        return record


def _failure_values(
    failed_phase: str | None,
    failed_detail: str | None,
) -> dict[str, object]:
    """Build bounded failure-detail column updates when provided."""
    values: dict[str, object] = {}
    if failed_phase is not None:
        values["failed_phase"] = failed_phase[:32]
    if failed_detail is not None:
        values["failed_detail"] = failed_detail[:500]
    return values


def _replacement_values(
    *,
    status: ResearchJobStatus,
    error_message: str | None,
    error_code: ResearchJobErrorCode | None,
    failed_phase: str | None,
    failed_detail: str | None,
    run_fingerprint: str | None,
    result_fingerprint: str | None,
    study_fingerprint: str | None,
    plan_fingerprint: str | None,
    progress_current: int | None,
    progress_total: int | None,
    cancel_requested: bool | None,
) -> dict[str, object]:
    """Build column updates for one research-job row (``None`` leaves a column alone)."""
    values: dict[str, object] = {
        "status": status.value,
        "updated_at": datetime.now(UTC),
    }
    if error_message is not None:
        values["error_message"] = error_message[:256]
    if error_code is not None:
        values["error_code"] = error_code.value
    values.update(_failure_values(failed_phase, failed_detail))
    optional: dict[str, object | None] = {
        "run_fingerprint": run_fingerprint,
        "result_fingerprint": result_fingerprint,
        "study_fingerprint": study_fingerprint,
        "plan_fingerprint": plan_fingerprint,
        "progress_current": progress_current,
        "progress_total": progress_total,
        "cancel_requested": cancel_requested,
    }
    values.update({key: value for key, value in optional.items() if value is not None})
    return values


def _error_code(value: object) -> ResearchJobErrorCode | None:
    """Narrow a stored error code; unknown legacy values read as absent."""
    if not isinstance(value, str):
        return None
    try:
        return ResearchJobErrorCode(value)
    except ValueError:
        return None


def _record_from_row(row: RowMapping) -> ResearchJobRecord:
    """Map one database row into a typed job record."""
    return ResearchJobRecord(
        job_id=cast("UUID", row["job_id"]),
        kind=ResearchJobKind(cast("str", row["kind"])),
        status=ResearchJobStatus(cast("str", row["status"])),
        created_at=cast("datetime", row["created_at"]),
        updated_at=cast("datetime", row["updated_at"]),
        expires_at=cast("datetime", row["expires_at"]),
        progress_current=cast("int", row["progress_current"]),
        progress_total=cast("int", row["progress_total"]),
        error_message=cast("str | None", row.get("error_message")),
        error_code=_error_code(row.get("error_code")),
        failed_phase=cast("str | None", row.get("failed_phase")),
        failed_detail=cast("str | None", row.get("failed_detail")),
        attempts=cast("int", row.get("attempts") or 0),
        run_fingerprint=cast("str | None", row.get("run_fingerprint")),
        result_fingerprint=cast("str | None", row.get("result_fingerprint")),
        study_fingerprint=cast("str | None", row.get("study_fingerprint")),
        plan_fingerprint=cast("str | None", row.get("plan_fingerprint")),
        strategy_id=UUID(cast("str", row["strategy_id"])),
        strategy_fingerprint=cast("str", row["strategy_fingerprint"]),
    )
