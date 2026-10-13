"""Advisory timing, separate from deterministic economics and execution authority."""

from __future__ import annotations

from decimal import Decimal
from time import perf_counter_ns, process_time_ns
from typing import TYPE_CHECKING

from thytrader.observability.database_calls import current_call_totals as database_totals
from thytrader.observability.venue_calls import current_call_totals as venue_totals
from thytrader.replay.steps import TimingEvidence

if TYPE_CHECKING:
    from thytrader.observability.venue_calls import CallTotals


def call_delta(
    current: CallTotals | None, earlier: CallTotals | None
) -> tuple[int | None, int | None]:
    """Use existing ADR 0131 counters; never relabel missing instrumentation as zero I/O."""
    if current is None or earlier is None:
        return None, None
    delta = current.since(earlier)
    return delta.requests, int(Decimal(str(max(delta.seconds, 0))) * Decimal("1000000000"))


class TimingObservation:
    """Observe the exact orchestration scope without changing database or venue instrumentation."""

    def __init__(self) -> None:
        """Pin monotonic elapsed/process CPU clocks and any already-bound I/O ledgers."""
        self.elapsed = perf_counter_ns()
        self.cpu = process_time_ns()
        self.database = database_totals()
        self.venue = venue_totals()
        self.finished: tuple[int, int] | None = None
        self.database_end: CallTotals | None = None
        self.venue_end: CallTotals | None = None

    def stop(self) -> None:
        """Pin computation and I/O counters at decision completion, before preparing evidence."""
        self.finished = (perf_counter_ns() - self.elapsed, process_time_ns() - self.cpu)
        self.database_end = database_totals()
        self.venue_end = venue_totals()

    def finish(self) -> TimingEvidence:
        """Label missing queue/wire boundaries; method calls are not wire sends."""
        if self.finished is None:
            raise ValueError("decision timing boundary was not observed")
        elapsed, cpu = self.finished
        database_count, database_ns = call_delta(self.database_end, self.database)
        venue_count, venue_ns = call_delta(self.venue_end, self.venue)
        unavailable = [
            "QUEUE_BOUNDARY_UNAVAILABLE",
            "WIRE_SEND_ACK_UNAVAILABLE",
            "CPU_PROCESS_SCOPE_NOT_EXCLUSIVE",
        ]
        if database_count is None:
            unavailable.append("DATABASE_INSTRUMENTATION_UNAVAILABLE")
        if venue_count is None:
            unavailable.append("VENUE_INSTRUMENTATION_UNAVAILABLE")
        return TimingEvidence(
            elapsed_ns=elapsed,
            computation_cpu_ns=cpu,
            database_calls=database_count,
            database_elapsed_ns=database_ns,
            venue_calls=venue_count,
            venue_elapsed_ns=venue_ns,
            unavailable_reasons=tuple(unavailable),
        )
