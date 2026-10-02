"""How research routes queue jobs and wait for the research worker (ADR 0092).

The API never runs research. A synchronous submit queues a job like ``?async=true``
does, then long-polls the job row for at most ``research_sync_wait_seconds``:

* completed: answer exactly as the inline submit did (201 and the same body);
* failed: map the job's ``error_code`` back to the 422 or 503 the inline submit raised;
* still queued or running at the bound: answer 202 with the job, like ``?async=true``,
  plus ``sync_wait_seconds`` so the caller knows it waited and should poll the job.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException, Request, status

from thytrader.research.jobs import (
    ResearchExecutionMode,
    ResearchJobErrorCode,
    ResearchJobRecord,
    ResearchJobStatus,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.research.jobs import ResearchJobStore

_FIRST_POLL_SECONDS: Final = 0.01
_MAX_POLL_SECONDS: Final = 0.25
_TERMINAL: Final = frozenset(
    {
        ResearchJobStatus.COMPLETED,
        ResearchJobStatus.FAILED,
        ResearchJobStatus.CANCELLED,
        ResearchJobStatus.EXPIRED,
    }
)
_CALLER_INPUT: Final = frozenset(
    {
        ResearchJobErrorCode.BACKTEST_WINDOW_REJECTED,
        ResearchJobErrorCode.STUDY_WINDOW_REJECTED,
        ResearchJobErrorCode.STUDY_BUDGET_EXCEEDED,
    }
)


def get_research_execution(request: Request) -> ResearchExecutionMode:
    """Return who executes this app's research jobs (set during startup)."""
    mode = getattr(request.app.state, "research_execution", ResearchExecutionMode.UNAVAILABLE)
    if isinstance(mode, ResearchExecutionMode):
        return mode
    return ResearchExecutionMode.UNAVAILABLE


def require_research_execution(mode: ResearchExecutionMode, *, detail: str) -> None:
    """Refuse a submission no research worker could ever run (503 with ``detail``).

    Without PostgreSQL there is no durable queue for the ``research-worker`` service to
    claim from, so queuing would leave the job ``queued`` forever.
    """
    if mode is ResearchExecutionMode.UNAVAILABLE:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


async def wait_for_job(
    store: ResearchJobStore,
    job_id: UUID,
    *,
    timeout_seconds: float,
) -> ResearchJobRecord | None:
    """Poll one job until it is terminal or ``timeout_seconds`` pass; return the last read.

    Polls start at 10 ms (an in-process harness finishes fast) and back off to 250 ms
    (one primary-key read per poll against PostgreSQL).
    """
    deadline = time.monotonic() + timeout_seconds
    pause = _FIRST_POLL_SECONDS
    record = await store.get(job_id)
    while record is not None and record.status not in _TERMINAL:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(pause, remaining))
        pause = min(_MAX_POLL_SECONDS, pause * 2)
        record = await store.get(job_id)
    return record


def is_terminal(record: ResearchJobRecord) -> bool:
    """Return whether a job reached a final status."""
    return record.status in _TERMINAL


def sync_failure(record: ResearchJobRecord, *, unavailable_detail: str) -> HTTPException:
    """Return the HTTP error an inline synchronous submit raised for this outcome.

    Caller-input codes are 422 ``{code, message}``; a cancelled job is 409
    ``research_job_cancelled``; anything else (worker lost, expired, unavailable) is the
    route's plain 503 ``unavailable_detail``.
    """
    code = record.error_code
    if record.status is ResearchJobStatus.FAILED and code in _CALLER_INPUT and code is not None:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": code.value, "message": record.error_message or ""},
        )
    if record.status is ResearchJobStatus.CANCELLED:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "research_job_cancelled",
                "message": f"Research job {record.job_id} was cancelled before it finished.",
            },
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=unavailable_detail,
    )
