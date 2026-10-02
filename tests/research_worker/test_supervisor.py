"""Supervisor behaviour with real spawned processes and stand-in targets (ADR 0092)."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time
from typing import TYPE_CHECKING

from pydantic import SecretStr

from tests.research_worker import targets
from thytrader.research_worker.supervisor import ResearchWorkerSupervisor, SupervisorConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from thytrader.research_worker.spec import WorkerProcessSpec


def _config(scratch: Path, *, pool_size: int, readiness: Path | None = None) -> SupervisorConfig:
    """Return a fast-supervising pool whose 'dataset root' is the scratch directory."""
    return SupervisorConfig(
        pool_size=pool_size,
        database_url=SecretStr("postgresql+asyncpg://unused@127.0.0.1:1/unused"),
        dataset_root=scratch,
        log_level="INFO",
        lease_seconds=3.0,
        max_jobs=5,
        max_rss_growth_bytes=64 * 2**20,
        max_attempts=3,
        readiness_file=readiness,
        supervise_seconds=0.05,
        stop_grace_seconds=5.0,
        max_backoff_seconds=30.0,
    )


def _spawns(scratch: Path) -> dict[int, list[str]]:
    """Return the lease owners each slot's spawns recorded, by slot."""
    found: dict[int, list[str]] = {}
    for path in scratch.iterdir():
        slot, _pid = path.name.split("-", 1)
        found.setdefault(int(slot), []).append(path.read_text(encoding="utf-8"))
    return found


class _Running:
    """A supervisor running on a background thread."""

    def __init__(self, supervisor: ResearchWorkerSupervisor) -> None:
        """Start ``supervisor.run`` on a thread."""
        self.supervisor = supervisor
        self.thread = threading.Thread(target=supervisor.run, daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 15.0) -> float:
        """Request a stop and return how long ``run`` took to return."""
        started = time.monotonic()
        self.supervisor.request_stop()
        self.thread.join(timeout)
        assert not self.thread.is_alive()
        return time.monotonic() - started


@contextmanager
def _run(
    scratch: Path,
    target: Callable[[WorkerProcessSpec], None],
    *,
    pool_size: int,
    readiness: Path | None = None,
) -> Iterator[_Running]:
    """Yield a running supervisor and always stop it."""
    supervisor = ResearchWorkerSupervisor(
        _config(scratch, pool_size=pool_size, readiness=readiness),
        target=target,
        host="test-host",
        supervisor_pid=4242,
    )
    running = _Running(supervisor)
    try:
        yield running
    finally:
        running.stop()


def _wait(predicate: Callable[[], bool], timeout: float = 20.0) -> None:
    """Poll until ``predicate`` holds."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never held"
        time.sleep(0.05)


def test_planned_recycles_are_replaced_at_once_with_fresh_owner_tokens(tmp_path: Path) -> None:
    """Exit 0 means recycle: every slot is respawned, each spawn with a new lease owner."""
    with _run(tmp_path, targets.exit_clean, pool_size=2) as running:
        _wait(
            lambda: (
                set(_spawns(tmp_path)) == {0, 1}
                and all(len(owners) >= 3 for owners in _spawns(tmp_path).values())
            )
        )
        views = running.supervisor.slots()
        assert all(view.crash_streak == 0 for view in views)
        assert all(view.last_exit_code in {0, None} for view in views)
    spawns = _spawns(tmp_path)
    assert set(spawns) == {0, 1}
    for slot, owners in spawns.items():
        assert len(set(owners)) == len(owners)
        assert all(owner.startswith(f"test-host/4242/{slot}/") for owner in owners)


def test_crashing_slots_back_off_exponentially(tmp_path: Path) -> None:
    """A slot that keeps crashing is restarted after 1 s, then 2 s, not in a tight loop."""
    with _run(tmp_path, targets.crash, pool_size=1) as running:
        time.sleep(2.5)
        view = running.supervisor.slots()[0]
        spawned = view.spawned
        assert view.last_exit_code == 3
        assert view.crash_streak >= 2
    assert 2 <= spawned <= 3


def test_stop_terminates_workers_gracefully_and_clears_readiness(tmp_path: Path) -> None:
    """SIGTERM reaches every worker; run returns well inside the grace period."""
    scratch = tmp_path / "spawns"
    scratch.mkdir()
    readiness = tmp_path / "ready" / "research-worker.ready"
    supervisor = ResearchWorkerSupervisor(
        _config(scratch, pool_size=2, readiness=readiness),
        target=targets.run_until_terminated,
        host="test-host",
        supervisor_pid=4242,
    )
    running = _Running(supervisor)
    _wait(lambda: len(list(scratch.iterdir())) == 2 and readiness.exists())
    pids = [view.pid for view in supervisor.slots()]
    elapsed = running.stop()
    assert elapsed < 5.0
    assert not readiness.exists()
    assert all(pid is not None for pid in pids)
    assert all(not view.alive for view in supervisor.slots())
