"""Durable async research jobs for long-running backtests and composed studies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import json
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
from thytrader.research.studies import (
    ResearchStudyError,
    ResearchStudyRequest,
    StudyFailedPhase,
    plan_fingerprint,
)

if TYPE_CHECKING:
    from thytrader.research.studies import ResearchStudyService

_logger = logging.getLogger(__name__)

MAX_CONCURRENT_RESEARCH_JOBS = 2
RESEARCH_JOB_EXPIRY_HOURS = 24
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


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
    progress_total: int = Field(default=0, ge=0)
    error_message: str | None = None
    failed_phase: str | None = None
    failed_detail: str | None = None
    run_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    study_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    plan_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)


class ResearchJobAcceptedResponse(BaseModel):
    """HTTP 202 body for one queued research submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    kind: ResearchJobKind
    status: ResearchJobStatus


class ResearchJobStore(Protocol):
    """Durable queue for async backtests and composed studies."""

    async def create_backtest(self, request: BacktestSubmissionRequest) -> ResearchJobRecord:
        """Insert one queued backtest job and return its initial record."""
        ...

    async def create_study(self, request: ResearchStudyRequest) -> ResearchJobRecord:
        """Insert one queued study job and return its initial record."""
        ...

    async def get(self, job_id: UUID) -> ResearchJobRecord | None:
        """Return one job record when it exists."""
        ...

    async def claim_next(self) -> tuple[UUID, ResearchJobKind, str] | None:
        """Claim the oldest queued job when capacity allows."""
        ...

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
    ) -> ResearchJobRecord:
        """Persist a caller-visible or redacted failure with optional detail."""
        ...

    async def mark_cancelled(self, job_id: UUID) -> ResearchJobRecord:
        """Persist one cancelled job."""
        ...

    async def cancel(self, job_id: UUID) -> ResearchJobRecord:
        """Cancel one queued or running job."""
        ...

    async def expire_stale(self) -> int:
        """Mark overdue queued or running jobs expired."""
        ...

    async def recover_interrupted(self) -> int:
        """Requeue running jobs after API restart."""
        ...

    async def load_backtest_request(self, job_id: UUID) -> BacktestSubmissionRequest:
        """Load the queued backtest payload for one job."""
        ...

    async def load_study_request(self, job_id: UUID) -> ResearchStudyRequest:
        """Load the queued study payload for one job."""
        ...

    async def is_cancel_requested(self, job_id: UUID) -> bool:
        """Return whether cancellation was requested for one job."""
        ...


@dataclass
class InMemoryResearchJobStore:
    """Track async research jobs for tests and API-only installs."""

    _records: dict[UUID, ResearchJobRecord] = field(default_factory=dict)
    _payloads: dict[UUID, str] = field(default_factory=dict)
    _cancelled: set[UUID] = field(default_factory=set)
    _running_count: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create_backtest(self, request: BacktestSubmissionRequest) -> ResearchJobRecord:
        """Insert one queued backtest job and return its initial record."""
        return await self._create(ResearchJobKind.BACKTEST, request.model_dump_json())

    async def create_study(self, request: ResearchStudyRequest) -> ResearchJobRecord:
        """Insert one queued study job and return its initial record."""
        return await self._create(ResearchJobKind.STUDY, request.model_dump_json())

    async def _create(self, kind: ResearchJobKind, payload: str) -> ResearchJobRecord:
        """Insert one queued job under the store lock."""
        now = datetime.now(UTC)
        record = ResearchJobRecord(
            job_id=uuid4(),
            kind=kind,
            status=ResearchJobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=RESEARCH_JOB_EXPIRY_HOURS),
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
        """Claim the oldest queued job when capacity allows."""
        async with self._lock:
            if self._running_count >= MAX_CONCURRENT_RESEARCH_JOBS:
                return None
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
            self._running_count += 1
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
        async with self._lock:
            self._running_count = max(0, self._running_count - 1)
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
        async with self._lock:
            self._running_count = max(0, self._running_count - 1)
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
        async with self._lock:
            self._running_count = max(0, self._running_count - 1)
        return await self._replace(
            job_id,
            status=ResearchJobStatus.FAILED,
            error_message=error_message[:256],
            failed_phase=failed_phase[:32] if failed_phase is not None else None,
            failed_detail=failed_detail[:500] if failed_detail is not None else None,
        )

    async def mark_cancelled(self, job_id: UUID) -> ResearchJobRecord:
        """Persist one cancelled job."""
        async with self._lock:
            self._running_count = max(0, self._running_count - 1)
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
                if record.status is ResearchJobStatus.RUNNING:
                    self._running_count = max(0, self._running_count - 1)
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
        """Requeue running jobs after API restart."""
        recovered = 0
        async with self._lock:
            self._running_count = 0
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
            updates: dict[str, object] = {
                "status": status,
                "updated_at": datetime.now(UTC),
            }
            if error_message is not None:
                updates["error_message"] = error_message
            updates.update(_record_failure_updates(failed_phase, failed_detail))
            if run_fingerprint is not None:
                updates["run_fingerprint"] = run_fingerprint
            if result_fingerprint is not None:
                updates["result_fingerprint"] = result_fingerprint
            if study_fingerprint is not None:
                updates["study_fingerprint"] = study_fingerprint
            if plan_fingerprint is not None:
                updates["plan_fingerprint"] = plan_fingerprint
            if progress_current is not None:
                updates["progress_current"] = progress_current
            if progress_total is not None:
                updates["progress_total"] = progress_total
            updated = current.model_copy(update=updates)
            self._records[job_id] = updated
            if status is ResearchJobStatus.CANCELLED:
                self._cancelled.discard(job_id)
            return updated


async def run_backtest_job(
    store: ResearchJobStore,
    submitter: BacktestSubmitter,
    job_id: UUID,
    request: BacktestSubmissionRequest,
) -> None:
    """Execute one queued backtest and update job status."""
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
    except BacktestSubmissionRejectedError as rejected:
        await store.mark_failed(job_id, error_message=str(rejected))
    except BacktestSubmissionError:
        await store.mark_failed(job_id, error_message="Backtest submission is unavailable.")
    except Exception:  # noqa: BLE001 - background jobs must not leak internal failures
        await store.mark_failed(job_id, error_message="Backtest submission is unavailable.")


async def run_study_job(
    store: ResearchJobStore,
    service: ResearchStudyService,
    job_id: UUID,
    request: ResearchStudyRequest,
) -> None:
    """Execute one queued composed study and update job status."""
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
    except BacktestSubmissionRejectedError as rejected:
        await store.mark_failed(
            job_id,
            error_message=str(rejected),
            failed_phase=StudyFailedPhase.SUBMIT_CHILDREN.value,
        )
    except ResearchStudyError as error:
        if "cancelled" in str(error).lower():
            await store.mark_cancelled(job_id)
            return
        await store.mark_failed(
            job_id,
            error_message=str(error),
            failed_phase=error.failed_phase,
            failed_detail=(str(error.__cause__) if error.__cause__ is not None else None),
        )
    except Exception as error:  # noqa: BLE001 - background jobs must not leak internal failures
        await store.mark_failed(
            job_id,
            error_message="Research study submission is unavailable.",
            failed_phase=StudyFailedPhase.UNKNOWN.value,
            failed_detail=str(error),
        )


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
    """Poll a durable queue and execute research jobs with admission limits."""

    store: ResearchJobStore
    submitter: BacktestSubmitter
    study_service: ResearchStudyService | None

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Process queued jobs until the stop event is set."""
        while not stop_event.is_set():
            await self.store.expire_stale()
            claimed = await self.store.claim_next()
            if claimed is None:
                await asyncio.sleep(0.5)
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
                    error_message="Research study submission is unavailable.",
                    failed_phase=StudyFailedPhase.UNKNOWN.value,
                )

    async def start(self, stop_event: asyncio.Event) -> asyncio.Task[None]:
        """Recover interrupted jobs and return the background processing task."""
        recovered = await self.store.recover_interrupted()
        if recovered:
            _logger.info("research_jobs_recovered count=%s", recovered)
        return asyncio.create_task(self.run_forever(stop_event), name="research-job-runner")


def canonical_job_payload(payload: object) -> str:
    """Serialize one queued job payload with stable JSON ordering."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
