"""Durable research jobs for backtests and composed studies (ADR 0092).

Every backtest and study, synchronous or ``?async=true``, is a row in ``research_jobs``.
The ``research-worker`` service claims rows with ``FOR UPDATE SKIP LOCKED`` and a
renewed lease, so research never runs inside the API process. The in-memory store and
:class:`ResearchJobRunner` remain only as the in-process test harness.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmitter,
)
from thytrader.research.dataset_binding import BoundDataset
from thytrader.research.studies import ResearchStudyError, ResearchStudyRequest, StudyFailedPhase
from thytrader.research.study_identity import plan_fingerprint
from thytrader.research.study_planning import StudyBudgetError, StudyPlanningError

if TYPE_CHECKING:
    from thytrader.research.studies import ResearchStudyService

_logger = logging.getLogger(__name__)

RESEARCH_JOB_EXPIRY_HOURS = 24
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_BACKTEST_UNAVAILABLE = "Backtest submission is unavailable."
_STUDY_UNAVAILABLE = "Research study submission is unavailable."


class ResearchJobKind(StrEnum):
    """Supported durable research job kinds."""

    BACKTEST = "backtest"
    STUDY = "study"


class ResearchJobStatus(StrEnum):
    """Lifecycle states for one queued research job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ResearchJobErrorCode(StrEnum):
    """Why a research job failed, in the vocabulary its synchronous route answers with.

    A synchronous submit waits for the job and maps these codes back to the HTTP
    status it returned before research moved to the worker: the ``*_rejected`` and
    ``study_budget_exceeded`` codes are caller input (422); the others are 503.
    """

    BACKTEST_WINDOW_REJECTED = "backtest_window_rejected"
    STUDY_WINDOW_REJECTED = "study_window_rejected"
    STUDY_BUDGET_EXCEEDED = "study_budget_exceeded"
    RESEARCH_UNAVAILABLE = "research_unavailable"
    RESEARCH_WORKER_LOST = "research_worker_lost"


class ResearchExecutionMode(StrEnum):
    """Who executes queued research jobs for one API process.

    ``research_worker`` is every PostgreSQL install: the API only validates, queues,
    and waits. ``in_process`` is the test harness that runs the queue inside the app.
    ``unavailable`` means no PostgreSQL and no harness, so research submissions 503.
    """

    RESEARCH_WORKER = "research_worker"
    IN_PROCESS = "in_process"
    UNAVAILABLE = "unavailable"


class ResearchJobLeaseLostError(RuntimeError):
    """Signal that a worker no longer holds the lease of the job it was running.

    Another worker re-queued or re-claimed the job (the lease expired), so the
    caller must stop writing to it and abandon the attempt.
    """


class ResearchJobRecord(BaseModel):
    """One async research submission tracked until completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    kind: ResearchJobKind
    status: ResearchJobStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    progress_current: int = Field(default=0, ge=0)
    progress_total: int = Field(default=1, ge=0)
    error_message: str | None = None
    error_code: ResearchJobErrorCode | None = None
    failed_phase: str | None = None
    failed_detail: str | None = None
    attempts: int = Field(default=0, ge=0)
    run_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    study_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    plan_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    strategy_id: UUID | None = None
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)


class ResearchJobAcceptedResponse(BaseModel):
    """HTTP 202 body for one queued research submission.

    ``strategy_fingerprint`` is the snapshot the job will run (for a study, its
    primary strategy's snapshot), taken when the request was accepted.
    ``bound_datasets`` echoes every dataset the job is bound to, including any the
    server chose from the catalog (ADR 0089). ``evaluation_start`` and
    ``evaluation_end`` are a study's evaluation window (filled from common dataset
    coverage when omitted); a backtest leaves them null because its omitted window
    is filled when the job runs. ``sync_wait_seconds`` is set only when a synchronous
    submit waited that long for the research worker and returned the still-queued or
    still-running job instead (ADR 0092); it is null for ``?async=true``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    kind: ResearchJobKind
    status: ResearchJobStatus
    strategy_id: UUID | None = None
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    bound_datasets: tuple[BoundDataset, ...] = ()
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    sync_wait_seconds: float | None = Field(default=None, ge=0)


class ResearchJobListResponse(BaseModel):
    """Newest-first async research jobs for one strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jobs: tuple[ResearchJobRecord, ...]
    limit: int
    returned: int


def primary_strategy_fingerprint(request: ResearchStudyRequest) -> str:
    """Return the snapshot a study is filed under: its base strategy or first market."""
    if request.strategy_fingerprint is not None:
        return request.strategy_fingerprint
    if request.markets:
        return request.markets[0].strategy_fingerprint
    raise StudyPlanningError("Study request names no strategy snapshot.")


class ResearchJobExecutionStore(Protocol):
    """The writes one job execution makes: status, progress, outcome, and cancel checks.

    A research worker passes a lease-fenced store here: every write also requires that
    the worker still holds the job's lease and raises :class:`ResearchJobLeaseLostError`
    when it does not, so a worker whose lease expired can never overwrite the attempt
    that replaced it.
    """

    async def mark_running(self, job_id: UUID) -> ResearchJobRecord:
        """Transition one job to running."""
        ...

    async def update_progress(
        self,
        job_id: UUID,
        *,
        progress_current: int,
        progress_total: int,
    ) -> ResearchJobRecord:
        """Persist bounded progress for one running job."""
        ...

    async def mark_completed_backtest(
        self,
        job_id: UUID,
        *,
        run_fingerprint: str,
        result_fingerprint: str,
    ) -> ResearchJobRecord:
        """Persist successful backtest completion fingerprints."""
        ...

    async def mark_completed_study(
        self,
        job_id: UUID,
        *,
        study_fingerprint: str,
        plan_fingerprint: str,
    ) -> ResearchJobRecord:
        """Persist successful study completion fingerprints."""
        ...

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
        ...

    async def mark_cancelled(self, job_id: UUID) -> ResearchJobRecord:
        """Persist one cancelled job."""
        ...

    async def is_cancel_requested(self, job_id: UUID) -> bool:
        """Return whether cancellation was requested for one job."""
        ...


class ResearchJobStore(ResearchJobExecutionStore, Protocol):
    """Durable queue for backtests and composed studies."""

    async def create_backtest(
        self, request: BacktestSubmissionRequest, *, strategy_id: UUID
    ) -> ResearchJobRecord:
        """Insert one queued backtest job owned by ``strategy_id``."""
        ...

    async def create_study(
        self, request: ResearchStudyRequest, *, strategy_id: UUID
    ) -> ResearchJobRecord:
        """Insert one queued study job owned by its primary ``strategy_id``."""
        ...

    async def list_for_strategy(
        self, strategy_id: UUID, *, limit: int
    ) -> tuple[ResearchJobRecord, ...]:
        """Return the strategy's newest jobs first."""
        ...

    async def get(self, job_id: UUID) -> ResearchJobRecord | None:
        """Return one job record when it exists."""
        ...

    async def claim_next(self) -> tuple[UUID, ResearchJobKind, str] | None:
        """Claim the oldest queued job for the in-process harness."""
        ...

    async def cancel(self, job_id: UUID) -> ResearchJobRecord:
        """Cancel one queued or running job."""
        ...

    async def expire_stale(self) -> int:
        """Mark overdue queued or running jobs expired."""
        ...

    async def recover_interrupted(self) -> int:
        """Requeue running jobs whose executor is gone."""
        ...

    async def load_backtest_request(self, job_id: UUID) -> BacktestSubmissionRequest:
        """Load the queued backtest payload for one job."""
        ...

    async def load_study_request(self, job_id: UUID) -> ResearchStudyRequest:
        """Load the queued study payload for one job."""
        ...


@dataclass
class InMemoryResearchJobStore:
    """Track research jobs for the in-process test harness and database-less installs."""

    _records: dict[UUID, ResearchJobRecord] = field(default_factory=dict)
    _payloads: dict[UUID, str] = field(default_factory=dict)
    _cancelled: set[UUID] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
        """Return the strategy's newest jobs first."""
        async with self._lock:
            rows = [item for item in self._records.values() if item.strategy_id == strategy_id]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(rows[:limit])

    async def _create(
        self,
        kind: ResearchJobKind,
        payload: str,
        *,
        strategy_id: UUID,
        strategy_fingerprint: str,
    ) -> ResearchJobRecord:
        """Insert one queued job under the store lock."""
        now = datetime.now(UTC)
        record = ResearchJobRecord(
            job_id=uuid4(),
            kind=kind,
            status=ResearchJobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=RESEARCH_JOB_EXPIRY_HOURS),
            strategy_id=strategy_id,
            strategy_fingerprint=strategy_fingerprint,
        )
        async with self._lock:
            self._records[record.job_id] = record
            self._payloads[record.job_id] = payload
        return record

    async def get(self, job_id: UUID) -> ResearchJobRecord | None:
        """Return one job record when it exists."""
        async with self._lock:
            return self._records.get(job_id)

    async def claim_next(self) -> tuple[UUID, ResearchJobKind, str] | None:
        """Claim the oldest queued job: mark it running and count the attempt."""
        async with self._lock:
            queued = sorted(
                (
                    (job_id, record)
                    for job_id, record in self._records.items()
                    if record.status is ResearchJobStatus.QUEUED
                ),
                key=lambda item: item[1].created_at,
            )
            if not queued:
                return None
            job_id, record = queued[0]
            self._records[job_id] = record.model_copy(
                update={
                    "status": ResearchJobStatus.RUNNING,
                    "attempts": record.attempts + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            return job_id, record.kind, self._payloads[job_id]

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
        async with self._lock:
            current = self._records.get(job_id)
            if current is None:
                message = f"Research job {job_id} was not found."
                raise KeyError(message)
            if current.status is ResearchJobStatus.COMPLETED:
                message = "Completed research jobs cannot be cancelled."
                raise ValueError(message)
            if current.status is ResearchJobStatus.RUNNING:
                self._cancelled.add(job_id)
                return current
            self._records[job_id] = current.model_copy(
                update={
                    "status": ResearchJobStatus.CANCELLED,
                    "updated_at": datetime.now(UTC),
                }
            )
            return self._records[job_id]

    async def expire_stale(self) -> int:
        """Mark overdue queued or running jobs expired."""
        now = datetime.now(UTC)
        expired = 0
        async with self._lock:
            for job_id, record in list(self._records.items()):
                if record.expires_at > now:
                    continue
                if record.status not in {
                    ResearchJobStatus.QUEUED,
                    ResearchJobStatus.RUNNING,
                }:
                    continue
                self._records[job_id] = record.model_copy(
                    update={
                        "status": ResearchJobStatus.EXPIRED,
                        "updated_at": now,
                        "error_message": "Research job expired.",
                    }
                )
                expired += 1
        return expired

    async def recover_interrupted(self) -> int:
        """Requeue running jobs when the in-process harness restarts."""
        recovered = 0
        async with self._lock:
            for job_id, record in list(self._records.items()):
                if record.status is not ResearchJobStatus.RUNNING:
                    continue
                self._records[job_id] = record.model_copy(
                    update={
                        "status": ResearchJobStatus.QUEUED,
                        "updated_at": datetime.now(UTC),
                    }
                )
                recovered += 1
        return recovered

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
        async with self._lock:
            return job_id in self._cancelled

    async def _payload(self, job_id: UUID) -> str:
        """Return the stored JSON payload for one job."""
        async with self._lock:
            payload = self._payloads.get(job_id)
        if payload is None:
            message = f"Research job {job_id} was not found."
            raise KeyError(message)
        return payload

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
    ) -> ResearchJobRecord:
        """Update one job under the store lock."""
        async with self._lock:
            current = self._records.get(job_id)
            if current is None:
                message = f"Research job {job_id} was not found."
                raise KeyError(message)
            optional: dict[str, object | None] = {
                "error_message": error_message,
                "error_code": error_code,
                "run_fingerprint": run_fingerprint,
                "result_fingerprint": result_fingerprint,
                "study_fingerprint": study_fingerprint,
                "plan_fingerprint": plan_fingerprint,
                "progress_current": progress_current,
                "progress_total": progress_total,
            }
            updates: dict[str, object] = {
                "status": status,
                "updated_at": datetime.now(UTC),
                **{key: value for key, value in optional.items() if value is not None},
                **_record_failure_updates(failed_phase, failed_detail),
            }
            updated = current.model_copy(update=updates)
            self._records[job_id] = updated
            if status is ResearchJobStatus.CANCELLED:
                self._cancelled.discard(job_id)
            return updated


async def run_backtest_job(
    store: ResearchJobExecutionStore,
    submitter: BacktestSubmitter,
    job_id: UUID,
    request: BacktestSubmissionRequest,
) -> None:
    """Execute one queued backtest and record its outcome with a failure code.

    Raises:
        ResearchJobLeaseLostError: The worker lost the job's lease; nothing was written.
    """
    try:
        if await store.is_cancel_requested(job_id):
            await store.mark_cancelled(job_id)
            return
        await store.mark_running(job_id)
        await store.update_progress(job_id, progress_current=0, progress_total=1)
        result = await submitter.submit(request)
        await store.update_progress(job_id, progress_current=1, progress_total=1)
        await store.mark_completed_backtest(
            job_id,
            run_fingerprint=result.run_fingerprint,
            result_fingerprint=result.result_fingerprint,
        )
    except ResearchJobLeaseLostError:
        raise
    except BacktestSubmissionRejectedError as rejected:
        await store.mark_failed(
            job_id,
            error_message=str(rejected),
            error_code=ResearchJobErrorCode.BACKTEST_WINDOW_REJECTED,
        )
    except BacktestSubmissionError:
        await store.mark_failed(
            job_id,
            error_message=_BACKTEST_UNAVAILABLE,
            error_code=ResearchJobErrorCode.RESEARCH_UNAVAILABLE,
        )
    except Exception:  # noqa: BLE001 - background jobs must not leak internal failures
        await store.mark_failed(
            job_id,
            error_message=_BACKTEST_UNAVAILABLE,
            error_code=ResearchJobErrorCode.RESEARCH_UNAVAILABLE,
        )


async def run_study_job(
    store: ResearchJobExecutionStore,
    service: ResearchStudyService,
    job_id: UUID,
    request: ResearchStudyRequest,
) -> None:
    """Execute one queued composed study and record its outcome with a failure code.

    Raises:
        ResearchJobLeaseLostError: The worker lost the job's lease; nothing was written.
    """
    try:
        if await store.is_cancel_requested(job_id):
            await store.mark_cancelled(job_id)
            return
        await store.mark_running(job_id)
        study = await service.submit_with_progress(
            request,
            on_progress=lambda current, total: store.update_progress(
                job_id,
                progress_current=current,
                progress_total=total,
            ),
            cancel_check=lambda: store.is_cancel_requested(job_id),
        )
        plan_fp = plan_fingerprint(
            await service.plan(request),
        )
        await store.mark_completed_study(
            job_id,
            study_fingerprint=study.study_fingerprint,
            plan_fingerprint=plan_fp,
        )
    except ResearchJobLeaseLostError:
        raise
    except StudyPlanningError as error:
        await store.mark_failed(
            job_id,
            error_message=str(error),
            failed_phase=StudyFailedPhase.PLAN.value,
            failed_detail=str(error),
            error_code=_planning_error_code(error),
        )
    except BacktestSubmissionRejectedError as rejected:
        await store.mark_failed(
            job_id,
            error_message=str(rejected),
            failed_phase=StudyFailedPhase.SUBMIT_CHILDREN.value,
            failed_detail=str(rejected),
            error_code=ResearchJobErrorCode.BACKTEST_WINDOW_REJECTED,
        )
    except ResearchStudyError as error:
        await _record_study_error(store, job_id, error)
    except Exception as error:  # noqa: BLE001 - background jobs must not leak internal failures
        await store.mark_failed(
            job_id,
            error_message=_STUDY_UNAVAILABLE,
            failed_phase=StudyFailedPhase.UNKNOWN.value,
            failed_detail=str(error),
            error_code=ResearchJobErrorCode.RESEARCH_UNAVAILABLE,
        )


async def _record_study_error(
    store: ResearchJobExecutionStore, job_id: UUID, error: ResearchStudyError
) -> None:
    """Record a study-service failure, or the cancellation it reports."""
    if isinstance(error.__cause__, ResearchJobLeaseLostError):
        raise error.__cause__
    if "cancelled" in str(error).lower():
        await store.mark_cancelled(job_id)
        return
    await store.mark_failed(
        job_id,
        error_message=str(error),
        failed_phase=error.failed_phase,
        failed_detail=(str(error.__cause__) if error.__cause__ is not None else None),
        error_code=ResearchJobErrorCode.RESEARCH_UNAVAILABLE,
    )


def _planning_error_code(error: StudyPlanningError) -> ResearchJobErrorCode:
    """Name a planning rejection the way the synchronous study route does."""
    if isinstance(error, StudyBudgetError):
        return ResearchJobErrorCode.STUDY_BUDGET_EXCEEDED
    return ResearchJobErrorCode.STUDY_WINDOW_REJECTED


def _record_failure_updates(
    failed_phase: str | None,
    failed_detail: str | None,
) -> dict[str, object]:
    """Build bounded failure-detail record updates when provided."""
    updates: dict[str, object] = {}
    if failed_phase is not None:
        updates["failed_phase"] = failed_phase
    if failed_detail is not None:
        updates["failed_detail"] = failed_detail
    return updates


@dataclass(frozen=True, slots=True)
class ResearchJobRunner:
    """In-process test harness: poll one store and run its jobs one at a time.

    Production never starts this. Every PostgreSQL install runs research in the
    ``research-worker`` service (ADR 0092); ``create_app`` starts this runner only when
    a test passes ``research_execution=ResearchExecutionMode.IN_PROCESS``.
    """

    store: ResearchJobStore
    submitter: BacktestSubmitter
    study_service: ResearchStudyService | None
    poll_seconds: float = 0.5

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Process queued jobs until the stop event is set."""
        while not stop_event.is_set():
            await self.store.expire_stale()
            claimed = await self.store.claim_next()
            if claimed is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            job_id, kind, _payload = claimed
            if kind is ResearchJobKind.BACKTEST:
                request = await self.store.load_backtest_request(job_id)
                await run_backtest_job(self.store, self.submitter, job_id, request)
            elif self.study_service is not None:
                request = await self.store.load_study_request(job_id)
                await run_study_job(self.store, self.study_service, job_id, request)
            else:
                await self.store.mark_failed(
                    job_id,
                    error_message=_STUDY_UNAVAILABLE,
                    failed_phase=StudyFailedPhase.UNKNOWN.value,
                    error_code=ResearchJobErrorCode.RESEARCH_UNAVAILABLE,
                )

    async def start(self, stop_event: asyncio.Event) -> asyncio.Task[None]:
        """Recover interrupted jobs and return the background processing task."""
        recovered = await self.store.recover_interrupted()
        if recovered:
            _logger.info("research_jobs_recovered count=%s", recovered)
        return asyncio.create_task(self.run_forever(stop_event), name="research-job-runner")
