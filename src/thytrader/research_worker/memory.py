"""Process memory and kernel hints for research worker processes (Linux ``/proc``).

Everything here is best-effort: on a platform without ``/proc`` or ``prctl`` the
helpers report "unknown" (``None`` / ``False``) instead of failing the worker.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
from pathlib import Path
import signal
from typing import Final

_logger = logging.getLogger(__name__)
_PR_SET_PDEATHSIG: Final = 1
_KIB: Final = 1024


def process_rss_bytes(pid: int | None = None) -> int | None:
    """Return the resident set size of ``pid`` (default: this process), or None."""
    path = Path("/proc/self/status") if pid is None else Path(f"/proc/{pid}/status")
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * _KIB
    return None


def raise_oom_score(adjustment: int) -> bool:
    """Ask the kernel to OOM-kill this process before calmer ones; return success.

    Raising ``oom_score_adj`` needs no privilege. A research worker is the most
    restartable process on the box: its job is re-queued when it dies.
    """
    bounded = max(-1000, min(1000, adjustment))
    try:
        Path("/proc/self/oom_score_adj").write_text(f"{bounded}\n", encoding="ascii")
    except OSError:
        return False
    return True


def die_with_parent(sig: signal.Signals = signal.SIGTERM) -> bool:
    """Have the kernel signal this process when its parent dies; return success.

    Without it, a supervisor that is killed outright would leave orphaned workers
    running jobs whose leases nobody renews.
    """
    library = ctypes.util.find_library("c")
    if library is None:
        return False
    try:
        libc = ctypes.CDLL(library, use_errno=True)
        result = int(libc.prctl(_PR_SET_PDEATHSIG, int(sig), 0, 0, 0))
    except OSError, AttributeError:
        _logger.debug("research_worker_pdeathsig_unavailable")
        return False
    return result == 0
