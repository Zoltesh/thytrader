"""Picklable configuration one research worker process starts from, and its recycle rule.

The supervisor resolves :class:`~thytrader.config.Settings` once and hands each spawned
process a :class:`WorkerProcessSpec`, so a worker never re-reads ``.env`` or the
environment and every slot runs with exactly the supervisor's configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import SecretStr

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
_MIB: Final = 1024 * 1024


class RecycleReason(StrEnum):
    """Why a worker process exits on its own so the supervisor replaces it."""

    MAX_JOBS = "max_jobs"
    RSS_GROWTH = "rss_growth"


class WorkerExit(StrEnum):
    """How one worker process's main loop ended."""

    STOPPED = "stopped"
    STOPPED_MID_JOB = "stopped_mid_job"
    RECYCLE = "recycle"


@dataclass(frozen=True, slots=True)
class WorkerProcessSpec:
    """Everything one research worker process needs (sent to it when it is spawned)."""

    slot: int
    pool_size: int
    lineage: str
    owner: str
    database_url: SecretStr
    dataset_root: Path
    log_level: LogLevel
    lease_seconds: float
    heartbeat_seconds: float
    poll_seconds: float
    max_jobs: int
    max_rss_growth_bytes: int
    max_attempts: int


def heartbeat_interval(lease_seconds: float) -> float:
    """Renew a lease six times per lease period (at least every 0.25 s, at most 10 s)."""
    return max(0.25, min(10.0, lease_seconds / 6))


def megabytes(value: int) -> int:
    """Convert MiB to bytes."""
    return value * _MIB


def recycle_reason(
    spec: WorkerProcessSpec,
    *,
    jobs_completed: int,
    rss_bytes: int | None,
    warm_rss_bytes: int | None,
) -> RecycleReason | None:
    """Return why this process should exit after its current job, or None to keep going.

    ``warm_rss_bytes`` is the RSS measured after the first job, when imports and the
    first datasets are resident; growth beyond it by ``max_rss_growth_bytes`` is creep.
    """
    if jobs_completed >= spec.max_jobs:
        return RecycleReason.MAX_JOBS
    if rss_bytes is None or warm_rss_bytes is None:
        return None
    if rss_bytes - warm_rss_bytes > spec.max_rss_growth_bytes:
        return RecycleReason.RSS_GROWTH
    return None
