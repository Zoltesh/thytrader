"""Recycle rules, lease identities, and process memory helpers (ADR 0092)."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
import pytest

from thytrader.research.worker_pool import (
    ResearchWorkerStateError,
    lease_lineage,
    lease_owner,
)
from thytrader.research_worker.memory import process_rss_bytes
from thytrader.research_worker.spec import (
    RecycleReason,
    WorkerProcessSpec,
    heartbeat_interval,
    megabytes,
    recycle_reason,
)


def _spec(*, max_jobs: int = 10, growth_mb: int = 100) -> WorkerProcessSpec:
    """Return one worker spec with the given recycle limits."""
    lineage = lease_lineage("host", 7, 0)
    return WorkerProcessSpec(
        slot=0,
        pool_size=2,
        lineage=lineage,
        owner=lease_owner(lineage, "abc"),
        database_url=SecretStr("postgresql+asyncpg://unused@127.0.0.1:1/unused"),
        dataset_root=Path("/unused"),
        log_level="INFO",
        lease_seconds=60.0,
        heartbeat_seconds=10.0,
        poll_seconds=0.5,
        max_jobs=max_jobs,
        max_rss_growth_bytes=megabytes(growth_mb),
        max_attempts=3,
    )


def test_recycle_after_max_jobs_or_rss_growth_beyond_the_warm_baseline() -> None:
    """A process recycles on job count or on creep above its post-first-job RSS."""
    spec = _spec(max_jobs=3, growth_mb=100)
    warm = megabytes(150)
    assert recycle_reason(spec, jobs_completed=1, rss_bytes=warm, warm_rss_bytes=warm) is None
    assert (
        recycle_reason(spec, jobs_completed=2, rss_bytes=megabytes(240), warm_rss_bytes=warm)
        is None
    )
    assert (
        recycle_reason(spec, jobs_completed=2, rss_bytes=megabytes(260), warm_rss_bytes=warm)
        is RecycleReason.RSS_GROWTH
    )
    assert (
        recycle_reason(spec, jobs_completed=3, rss_bytes=warm, warm_rss_bytes=warm)
        is RecycleReason.MAX_JOBS
    )
    assert recycle_reason(spec, jobs_completed=1, rss_bytes=None, warm_rss_bytes=warm) is None


def test_heartbeat_renews_six_times_per_lease_within_bounds() -> None:
    """The lease is renewed well before it lapses, never faster than 4 Hz or slower than 10 s."""
    assert heartbeat_interval(60) == 10.0
    assert heartbeat_interval(3) == 0.5
    assert heartbeat_interval(1) == 0.25
    assert heartbeat_interval(3600) == 10.0


def test_lease_tokens_share_a_slot_lineage_and_stay_bounded() -> None:
    """Owner tokens are lineage plus nonce; unsafe host characters never reach a token."""
    lineage = lease_lineage("box_1/a b", 12, 3)
    assert lineage == "box-1-a-b/12/3/"
    assert lease_owner(lineage, "n0nce").startswith(lineage)
    with pytest.raises(ResearchWorkerStateError):
        lease_owner(lineage, "")
    with pytest.raises(ResearchWorkerStateError):
        lease_owner(lineage, "x" * 96)
    with pytest.raises(ResearchWorkerStateError):
        lease_lineage("host", 0, 0)


def test_process_rss_reads_proc_status() -> None:
    """This process has a positive RSS; a missing pid reads as unknown."""
    rss = process_rss_bytes()
    assert rss is None or rss > 0
    assert process_rss_bytes(2**31 - 7) is None
