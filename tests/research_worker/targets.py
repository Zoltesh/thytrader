"""Spawnable stand-ins for the research worker process used by supervisor tests.

Each target records its spawn as a file named ``<slot>-<pid>`` holding its lease owner
in ``spec.dataset_root`` (a scratch directory in these tests), then behaves as named.
"""

from __future__ import annotations

import os
import signal
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType

    from thytrader.research_worker.spec import WorkerProcessSpec


def _record(spec: WorkerProcessSpec) -> None:
    """Write this spawn's lease owner where the test can read it."""
    (spec.dataset_root / f"{spec.slot}-{os.getpid()}").write_text(spec.owner, encoding="utf-8")


def exit_clean(spec: WorkerProcessSpec) -> None:
    """Exit 0 at once, the way a worker exits for a planned recycle."""
    _record(spec)


def crash(spec: WorkerProcessSpec) -> None:
    """Exit non-zero at once, the way a crashed worker does."""
    _record(spec)
    os._exit(3)


def run_until_terminated(spec: WorkerProcessSpec) -> None:
    """Run until SIGTERM, then exit 0 (a graceful stop)."""
    stopped = False

    def stop(signum: int, frame: FrameType | None) -> None:
        """Note the stop request."""
        del signum, frame
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    _record(spec)
    while not stopped:
        time.sleep(0.02)
