"""``thytrader-research-worker``: run the research worker pool (ADR 0092)."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from typing import TYPE_CHECKING

from thytrader.config import Settings
from thytrader.observability.logging import configure_logging
from thytrader.research_worker.supervisor import ResearchWorkerSupervisor, SupervisorConfig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType

_logger = logging.getLogger(__name__)

_DESCRIPTION = """\
Run the research worker pool: the only process that executes backtests, composed
studies (OOS, walk-forward, sweep, WFO, cross-market), and portfolio backtests.
The API only validates, queues, and waits; a queued job waits here for a free worker.

Configuration comes from the environment (THYTRADER_*), never from flags:
  THYTRADER_DATABASE_URL                 PostgreSQL holding research_jobs (required)
  THYTRADER_MARKET_DATA_DATASET_ROOT     Verified dataset root (read-only is enough)
  THYTRADER_RESEARCH_WORKER_COUNT        Worker processes = concurrent jobs (default 2)
  THYTRADER_RESEARCH_WORKER_MAX_JOBS     Recycle a process after this many jobs (50)
  THYTRADER_RESEARCH_WORKER_MAX_RSS_GROWTH_MB
                                         Recycle after this much RSS growth (256)
  THYTRADER_RESEARCH_JOB_LEASE_SECONDS   Lease a dead worker's job is held for (60)
  THYTRADER_RESEARCH_JOB_MAX_ATTEMPTS    Claims before a crashing job fails (3)
  THYTRADER_RESEARCH_WORKER_READINESS_FILE
                                         Marker file for container health checks

Check liveness, queue depth, and per-worker RSS with `thytrader-operator health`.
"""


def build_parser() -> argparse.ArgumentParser:
    """Return the (flag-free) argument parser, which exists to document the service."""
    return argparse.ArgumentParser(
        prog="thytrader-research-worker",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Start the supervisor and block until SIGTERM or SIGINT."""
    build_parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings)
    try:
        config = SupervisorConfig.from_settings(settings)
    except ValueError as error:
        print(f"thytrader-research-worker: {error}", file=sys.stderr)  # noqa: T201 - CLI error.
        return 2
    supervisor = ResearchWorkerSupervisor(config)

    def stop(signum: int, frame: FrameType | None) -> None:
        """Translate a shutdown signal into a graceful pool stop."""
        del frame
        _logger.info("research_worker_stop_requested signal=%s", signum)
        supervisor.request_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    supervisor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
