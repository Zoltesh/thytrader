"""Execution-worker cycle timing for operator health and runtime (ADR 0131).

Reads the worker's recent cycle records and grades the newest against its budget: the
configured interval plus the 30 s slack health grants every worker loop. A completed cycle
over budget, or a cycle still running past it, is ``CYCLE_SLOW``, and the detail names the
slowest phase, the venue and database time and the slowest books. Missing or unreadable
telemetry is degraded, never healthy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median
from typing import TYPE_CHECKING

from pydantic import Field

from thytrader.observability.execution_cycle import (
    CyclePhaseName,
    ExecutionCycleRecord,
    ExecutionCycleReport,
    cycle_budget_seconds,
)
from thytrader.operator.models import ComponentReport, ReportStatus, _FrozenModel
from thytrader.persistence.execution_cycles import ExecutionCycleStoreUnavailableError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.persistence.execution_cycles import ExecutionCycleStore

RECENT_CYCLES = 20
_COMPONENT = "execution_cycle"
_DETAIL_LIMIT = 500
_DETAIL_BOOKS = 3


class ExecutionCycleSample(_FrozenModel):
    """One recent cycle's start and duration (``None`` while running or if it never ended)."""

    started_at: datetime
    duration_seconds: float | None = None
    venue_requests: int | None = None


class ExecutionCycleSummary(_FrozenModel):
    """Health's compact view of the worker cycle against its interval."""

    interval_seconds: int = Field(ge=1)
    budget_seconds: int = Field(ge=1)
    last_duration_seconds: float | None = None
    last_completed_at: datetime | None = None
    slowest_phase: CyclePhaseName | None = None
    in_progress_seconds: float | None = None
    slow: bool


class ExecutionCyclePayload(_FrozenModel):
    """Runtime's view: the newest completed cycle report plus recent durations.

    ``in_progress_started_at`` is set when the newest recorded cycle has not completed;
    ``latest`` is the newest completed cycle. ``recent`` lists up to 20 cycles, newest first.
    """

    summary: ExecutionCycleSummary
    in_progress_started_at: datetime | None = None
    latest: ExecutionCycleReport | None = None
    recent: tuple[ExecutionCycleSample, ...] = ()
    recent_median_seconds: float | None = None
    recent_max_seconds: float | None = None
    recent_slow_cycles: int = Field(default=0, ge=0)


async def execution_cycle_health(
    store: ExecutionCycleStore | None, *, now: datetime | None = None
) -> tuple[ComponentReport, ExecutionCyclePayload | None] | None:
    """Grade the newest execution cycle; ``None`` when this process has no cycle store."""
    if store is None:
        return None
    try:
        records = await store.recent_cycles(RECENT_CYCLES)
    except ExecutionCycleStoreUnavailableError:
        return (
            ComponentReport(
                name=_COMPONENT,
                status=ReportStatus.DEGRADED,
                reason_code="CYCLE_TIMING_UNAVAILABLE",
                detail="Execution cycle timing could not be read from PostgreSQL.",
            ),
            None,
        )
    if not records:
        return (
            ComponentReport(
                name=_COMPONENT,
                status=ReportStatus.DEGRADED,
                reason_code="CYCLE_TIMING_MISSING",
                detail="The execution worker has not recorded a cycle yet.",
            ),
            None,
        )
    payload = _payload(records, now=now or datetime.now(UTC))
    return _grade(payload), payload


def _payload(records: Sequence[ExecutionCycleRecord], *, now: datetime) -> ExecutionCyclePayload:
    """Summarize newest-first records into the runtime payload."""
    newest = records[0]
    latest = next((record.report for record in records if record.report is not None), None)
    in_progress = newest.started_at if newest.report is None else None
    in_progress_seconds = (
        None if in_progress is None else max((now - in_progress).total_seconds(), 0.0)
    )
    interval = newest.interval_seconds
    budget = cycle_budget_seconds(interval)
    durations = [record.report.duration_seconds for record in records if record.report is not None]
    summary = ExecutionCycleSummary(
        interval_seconds=interval,
        budget_seconds=budget,
        last_duration_seconds=None if latest is None else latest.duration_seconds,
        last_completed_at=None if latest is None else latest.completed_at,
        slowest_phase=None if latest is None else latest.slowest_phase,
        in_progress_seconds=None if in_progress_seconds is None else round(in_progress_seconds, 1),
        slow=(in_progress_seconds is not None and in_progress_seconds > budget)
        or (latest is not None and latest.slow),
    )
    return ExecutionCyclePayload(
        summary=summary,
        in_progress_started_at=in_progress,
        latest=latest,
        recent=tuple(
            ExecutionCycleSample(
                started_at=record.started_at,
                duration_seconds=None if record.report is None else record.report.duration_seconds,
                venue_requests=None if record.report is None else record.report.venue.requests,
            )
            for record in records
        ),
        recent_median_seconds=round(median(durations), 3) if durations else None,
        recent_max_seconds=max(durations) if durations else None,
        recent_slow_cycles=sum(
            1 for record in records if record.report is not None and record.report.slow
        ),
    )


def _grade(payload: ExecutionCyclePayload) -> ComponentReport:
    """Return ``CYCLE_SLOW`` when the cycle overran its budget, else a healthy reading."""
    summary = payload.summary
    if summary.slow:
        return ComponentReport(
            name=_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="CYCLE_SLOW",
            detail=_slow_detail(payload)[:_DETAIL_LIMIT],
        )
    if payload.latest is None:
        return ComponentReport(
            name=_COMPONENT,
            status=ReportStatus.HEALTHY,
            reason_code="CYCLE_IN_PROGRESS",
            detail=(
                f"The first recorded cycle has run {summary.in_progress_seconds or 0:.1f}s "
                f"of its {summary.budget_seconds}s budget ({summary.interval_seconds}s interval)."
            ),
        )
    latest = payload.latest
    return ComponentReport(
        name=_COMPONENT,
        status=ReportStatus.HEALTHY,
        reason_code="CYCLE_WITHIN_BUDGET",
        detail=(
            f"The last cycle took {latest.duration_seconds:.1f}s against a "
            f"{latest.interval_seconds}s interval ({summary.budget_seconds}s budget; "
            f"{latest.venue.requests} venue requests, median of recent cycles "
            f"{payload.recent_median_seconds or 0:.1f}s)."
        ),
    )


def _slow_detail(payload: ExecutionCyclePayload) -> str:
    """Name the overrun, the slowest phase, venue traffic and the slowest books."""
    summary = payload.summary
    latest = payload.latest
    parts: list[str] = []
    if summary.in_progress_seconds is not None and summary.in_progress_seconds > (
        summary.budget_seconds
    ):
        parts.append(
            f"The current cycle has run {summary.in_progress_seconds:.1f}s, over its "
            f"{summary.budget_seconds}s budget ({summary.interval_seconds}s interval)."
        )
    if latest is None:
        return " ".join(parts)
    phase = next(item for item in latest.phases if item.name == latest.slowest_phase)
    parts.append(
        f"Last cycle took {latest.duration_seconds:.0f}s against a {latest.interval_seconds}s "
        f"interval ({summary.budget_seconds}s budget); slowest phase {phase.name} "
        f"{phase.seconds:.0f}s; venue "
        f"{latest.venue.requests} requests {latest.venue.seconds:.0f}s; database "
        f"{latest.database.statements} statements {latest.database.seconds:.0f}s."
    )
    books = [
        f"{book.deployment_id} {book.product_id} {book.timeframe or '-'} "
        f"{book.status} {book.seconds:.1f}s"
        for book in latest.slowest_books[:_DETAIL_BOOKS]
    ]
    if books:
        parts.append("Slowest books: " + "; ".join(books) + ".")
    return " ".join(parts)
