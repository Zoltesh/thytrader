"""Research worker pool vocabulary: claims, lease owners, slots, and queue depth (ADR 0092).

Backtests, studies, and portfolio backtests are durable rows in two tables
(``research_jobs`` and ``portfolio_backtest_jobs``). Each ``research-worker`` process
claims one row at a time with ``FOR UPDATE SKIP LOCKED`` and stamps it with a lease
owner token and a lease expiry that its heartbeat thread keeps renewing. A row whose
lease expired belongs to a dead worker and is re-queued (or failed after
``research_job_max_attempts`` claims).

Lease owner tokens are ``<host>/<supervisor pid>/<slot>/<nonce>``. Everything before
the nonce is the slot *lineage*: a replacement process for the same slot of the same
supervisor knows its predecessor is gone and re-queues the predecessor's rows at once
instead of waiting for the lease to expire.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ResearchQueueName = Literal["research_jobs", "portfolio_backtest_jobs"]
ResearchWorkKind = Literal["backtest", "study", "portfolio_backtest"]
ResearchWorkerState = Literal["starting", "idle", "running", "stopping"]

RESEARCH_QUEUES: Final[tuple[ResearchQueueName, ...]] = (
    "research_jobs",
    "portfolio_backtest_jobs",
)
LEASE_OWNER_MAX_LENGTH: Final = 96
_UNSAFE_HOST_CHARACTERS: Final = re.compile(r"[^A-Za-z0-9.-]")
WORKER_LOST_MESSAGE: Final = (
    "The research worker stopped while running this job on every allowed attempt; "
    "it was not retried."
)


class ResearchWorkerStateError(ValueError):
    """Reject a malformed lease lineage or slot identity."""


@dataclass(frozen=True, slots=True)
class ClaimedResearchJob:
    """One row a worker claimed: which queue, which job, what kind, which attempt."""

    queue: ResearchQueueName
    job_id: UUID
    kind: ResearchWorkKind
    attempts: int


@dataclass(frozen=True, slots=True)
class RequeueCounts:
    """What one lease sweep did to running rows whose worker is gone."""

    requeued: int = 0
    cancelled: int = 0
    failed: int = 0

    def __add__(self, other: RequeueCounts) -> RequeueCounts:
        """Sum two sweeps (for example, the two queues)."""
        return RequeueCounts(
            requeued=self.requeued + other.requeued,
            cancelled=self.cancelled + other.cancelled,
            failed=self.failed + other.failed,
        )

    @property
    def total(self) -> int:
        """Return how many rows the sweep changed."""
        return self.requeued + self.cancelled + self.failed


def lease_lineage(host: str, supervisor_pid: int, slot: int) -> str:
    """Return the token prefix every process of one supervisor slot shares.

    The host is reduced to ``[A-Za-z0-9.-]`` (other characters become ``-``) so a
    token never contains the ``/`` separator.
    """
    if supervisor_pid < 1 or slot < 0:
        message = "Research worker slots need a positive supervisor pid and a slot >= 0."
        raise ResearchWorkerStateError(message)
    safe_host = _UNSAFE_HOST_CHARACTERS.sub("-", host)[:48] or "host"
    return f"{safe_host}/{supervisor_pid}/{slot}/"


def lease_owner(lineage: str, nonce: str) -> str:
    """Return one process's lease owner token inside its slot lineage."""
    token = f"{lineage}{nonce}"
    if not lineage.endswith("/") or not nonce or len(token) > LEASE_OWNER_MAX_LENGTH:
        message = "Research worker lease owners are '<lineage>/<nonce>' up to 96 characters."
        raise ResearchWorkerStateError(message)
    return token


class _FrozenPoolModel(BaseModel):
    """Strict, immutable base for worker-pool report models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchWorkerSlot(_FrozenPoolModel):
    """The latest self-report of one research worker process (one row per slot)."""

    slot: int = Field(ge=0)
    pool_size: int = Field(ge=1)
    pid: int = Field(ge=1)
    state: ResearchWorkerState
    job_id: UUID | None = None
    job_kind: ResearchWorkKind | None = None
    jobs_completed: int = Field(ge=0)
    rss_bytes: int | None = Field(default=None, ge=0)
    started_at: datetime
    heartbeat_at: datetime


class ResearchQueueDepth(_FrozenPoolModel):
    """Queued and running rows of one queue and the oldest queued row's creation time."""

    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    oldest_queued_at: datetime | None = None


class ResearchWorkerPoolSnapshot(_FrozenPoolModel):
    """Both queues plus every worker slot row, read in one go for health reports."""

    research_jobs: ResearchQueueDepth
    portfolio_backtests: ResearchQueueDepth
    workers: tuple[ResearchWorkerSlot, ...]
