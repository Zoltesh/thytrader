"""PostgreSQL repository for durable async research jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.backtest.submission import BacktestSubmissionRequest
from thytrader.persistence.schema import research_jobs
from thytrader.research.jobs import (
    MAX_CONCURRENT_RESEARCH_JOBS,
    RESEARCH_JOB_EXPIRY_HOURS,
    ResearchJobKind,
    ResearchJobRecord,
    ResearchJobStatus,
)
from thytrader.research.studies import ResearchStudyRequest

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine


class ResearchJobUnavailableError(RuntimeError):
    """Signal that durable research-job storage is disabled or unreachable."""


class PostgresResearchJobStore:
    """Durable queue for async backtests and composed studies."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def create_backtest(self, request: BacktestSubmissionRequest) -> ResearchJobRecord:
        """Insert one queued backtest job and return its initial record."""
        return await self._create(ResearchJobKind.BACKTEST, request.model_dump_json())

    async def create_study(self, request: ResearchStudyRequest) -> ResearchJobRecord:
        """Insert one queued study job and return its initial record."""
        return await self._create(ResearchJobKind.STUDY, request.model_dump_json())

    async def _create(self, kind: ResearchJobKind, payload: str) -> ResearchJobRecord:
        """Insert one queued job row."""
        now = datetime.now(UTC)
        job_id = uuid4()
        statement = insert(research_jobs).values(
            job_id=job_id,
            kind=kind.value,
            status=ResearchJobStatus.QUEUED.value,
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
        """Claim the oldest queued job when capacity allows."""
        running_count = await self._running_count()
        if running_count >= MAX_CONCURRENT_RESEARCH_JOBS:
            return None
        statement = (
            select(research_jobs.c.job_id, research_jobs.c.kind, research_jobs.c.payload)
            .where(research_jobs.c.status == ResearchJobStatus.QUEUED.value)
            .order_by(research_jobs.c.created_at.asc())
            .limit(1)
        )
        try:
            async with self._engine.begin() as connection:
                row = (await connection.execute(statement)).one_or_none()
                if row is None:
                    return None
                job_id = cast("UUID", row[0])
                await connection.execute(
                    update(research_jobs)
                    .where(research_jobs.c.job_id == job_id)
                    .values(
                        status=ResearchJobStatus.RUNNING.value,
                        updated_at=datetime.now(UTC),
                    )
                )
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        return job_id, ResearchJobKind(cast("str", row[1])), cast("str", row[2])

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
    ) -> ResearchJobRecord:
        """Persist a caller-visible or redacted failure with optional detail."""
        return await self._replace(
            job_id,
            status=ResearchJobStatus.FAILED,
            error_message=error_message[:256],
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
        """Requeue running jobs after API restart."""
        now = datetime.now(UTC)
        statement = (
            update(research_jobs)
            .where(research_jobs.c.status == ResearchJobStatus.RUNNING.value)
            .values(status=ResearchJobStatus.QUEUED.value, updated_at=now)
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        return int(result.rowcount or 0)

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

    async def _running_count(self) -> int:
        """Count currently running jobs."""
        statement = select(func.count()).where(
            research_jobs.c.status == ResearchJobStatus.RUNNING.value
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).scalar_one()
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
        return int(row)

    async def _replace(
        self,
        job_id: UUID,
        *,
        status: ResearchJobStatus,
        error_message: str | None = None,
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
        """Update one job row."""
        values = _replacement_values(
            status=status,
            error_message=error_message,
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
        statement = update(research_jobs).where(research_jobs.c.job_id == job_id).values(**values)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchJobUnavailableError("Research jobs are unavailable.") from error
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
    """Build column updates for one research-job row."""
    values: dict[str, object] = {
        "status": status.value,
        "updated_at": datetime.now(UTC),
    }
    if error_message is not None:
        values["error_message"] = error_message[:256]
    values.update(_failure_values(failed_phase, failed_detail))
    if run_fingerprint is not None:
        values["run_fingerprint"] = run_fingerprint
    if result_fingerprint is not None:
        values["result_fingerprint"] = result_fingerprint
    if study_fingerprint is not None:
        values["study_fingerprint"] = study_fingerprint
    if plan_fingerprint is not None:
        values["plan_fingerprint"] = plan_fingerprint
    if progress_current is not None:
        values["progress_current"] = progress_current
    if progress_total is not None:
        values["progress_total"] = progress_total
    if cancel_requested is not None:
        values["cancel_requested"] = cancel_requested
    return values


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
        failed_phase=cast("str | None", row.get("failed_phase")),
        failed_detail=cast("str | None", row.get("failed_detail")),
        run_fingerprint=cast("str | None", row.get("run_fingerprint")),
        result_fingerprint=cast("str | None", row.get("result_fingerprint")),
        study_fingerprint=cast("str | None", row.get("study_fingerprint")),
        plan_fingerprint=cast("str | None", row.get("plan_fingerprint")),
    )
