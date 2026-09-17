"""In-process async backtest job tracking for long-running research submissions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmitter,
)


class BacktestJobStatus(StrEnum):
    """Lifecycle states for one queued backtest submission."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BacktestJobRecord(BaseModel):
    """One async backtest submission tracked until completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    status: BacktestJobStatus
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None
    run_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    result_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class BacktestJobAcceptedResponse(BaseModel):
    """HTTP 202 body for one queued backtest submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    status: BacktestJobStatus


@dataclass
class InMemoryBacktestJobStore:
    """Track async backtest jobs for the current API process."""

    _records: dict[UUID, BacktestJobRecord] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create(self) -> BacktestJobRecord:
        """Insert one queued job and return its initial record."""
        now = datetime.now(UTC)
        record = BacktestJobRecord(
            job_id=uuid4(),
            status=BacktestJobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._records[record.job_id] = record
        return record

    async def get(self, job_id: UUID) -> BacktestJobRecord | None:
        """Return one job record when it exists."""
        async with self._lock:
            return self._records.get(job_id)

    async def mark_running(self, job_id: UUID) -> BacktestJobRecord:
        """Transition one job to running."""
        return await self._replace(job_id, status=BacktestJobStatus.RUNNING)

    async def mark_completed(
        self,
        job_id: UUID,
        *,
        run_fingerprint: str,
        result_fingerprint: str,
    ) -> BacktestJobRecord:
        """Persist successful completion fingerprints."""
        return await self._replace(
            job_id,
            status=BacktestJobStatus.COMPLETED,
            run_fingerprint=run_fingerprint,
            result_fingerprint=result_fingerprint,
        )

    async def mark_failed(self, job_id: UUID, *, error_message: str) -> BacktestJobRecord:
        """Persist a caller-visible or redacted failure."""
        return await self._replace(
            job_id,
            status=BacktestJobStatus.FAILED,
            error_message=error_message,
        )

    async def _replace(
        self,
        job_id: UUID,
        *,
        status: BacktestJobStatus,
        error_message: str | None = None,
        run_fingerprint: str | None = None,
        result_fingerprint: str | None = None,
    ) -> BacktestJobRecord:
        """Update one job under the store lock."""
        async with self._lock:
            current = self._records.get(job_id)
            if current is None:
                message = f"Backtest job {job_id} was not found."
                raise KeyError(message)
            updated = current.model_copy(
                update={
                    "status": status,
                    "updated_at": datetime.now(UTC),
                    "error_message": error_message,
                    "run_fingerprint": run_fingerprint,
                    "result_fingerprint": result_fingerprint,
                }
            )
            self._records[job_id] = updated
            return updated


async def run_backtest_job(
    store: InMemoryBacktestJobStore,
    submitter: BacktestSubmitter,
    job_id: UUID,
    request: BacktestSubmissionRequest,
) -> None:
    """Execute one queued backtest and update job status."""
    try:
        await store.mark_running(job_id)
        result = await submitter.submit(request)
        await store.mark_completed(
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
