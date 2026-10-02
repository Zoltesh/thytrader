"""Spawn target for research worker processes; deliberately light to import.

The supervisor pickles :func:`worker_process_main` by reference when it spawns a
worker, which imports this module in the supervisor too. Keeping the research stack
(SQLAlchemy, the simulation engine, Polars) out of this module's imports keeps the
supervisor at a few tens of MB instead of the ~100 MB a worker needs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING, Final

from thytrader.config import Settings
from thytrader.observability.logging import configure_logging
from thytrader.research_worker.memory import die_with_parent, raise_oom_score

if TYPE_CHECKING:
    from thytrader.research_worker.spec import WorkerProcessSpec

WORKER_OOM_SCORE_ADJ: Final = 800
_logger = logging.getLogger(__name__)


def worker_process_main(spec: WorkerProcessSpec) -> None:
    """Configure one spawned worker process, run it, and exit with its outcome.

    Exit code 0 means a clean stop or a planned recycle; anything else is a crash
    the supervisor restarts with backoff. A stop that arrived mid-job exits through
    ``os._exit`` so a simulation thread cannot hold the process open.
    """
    # Function-local import (documented reason): importing the worker stack here, in the
    # spawned child only, keeps the supervisor process from loading it (see module doc).
    from thytrader.research_worker.process import run_worker_process  # noqa: PLC0415
    from thytrader.research_worker.spec import WorkerExit  # noqa: PLC0415

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    die_with_parent()
    raise_oom_score(WORKER_OOM_SCORE_ADJ)
    configure_logging(
        Settings(
            _env_file=None,
            database_url=spec.database_url,
            log_level=spec.log_level,
            market_data_dataset_root=spec.dataset_root,
        )
    )
    _logger.info("research_worker_process_started slot=%s pid=%s", spec.slot, os.getpid())
    outcome = asyncio.run(run_worker_process(spec))
    _logger.info("research_worker_process_exiting slot=%s outcome=%s", spec.slot, outcome.value)
    if outcome is WorkerExit.STOPPED_MID_JOB:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
