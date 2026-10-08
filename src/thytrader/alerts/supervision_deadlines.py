"""Supervision deadline findings: missed decisions, stale worker leases, and maintenance.

Decision deadlines follow the venue clock plus the publication settling grace, so a
late provider publication never produces a false stale alert. Lease and maintenance
findings compare the worker lease against the worker interval and lease TTL.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.alerts.models import (
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.execution.leases import WORKER_LEASE_TTL_SECONDS
from thytrader.market_data.models import parse_candle_interval
from thytrader.trading.geometry import entry_bar_bucket
from thytrader.trading.models import (
    Deployment,
    DeploymentStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.alerts.supervision_inputs import AlertThresholds

#: Publication settling grace for decision deadlines, aligned with ADR 0104's
#: fixed 120-second newest-candle wait. A missed-decision alert fires only after
#: the oldest missed bar's close plus this grace has passed, so a late provider
#: publication never produces a false stale alert.
SETTLING_GRACE_SECONDS = 120.0


def _decision_deadline_finding(
    deployment: Deployment, *, now: datetime, thresholds: AlertThresholds
) -> SupervisionFinding | None:
    """Alert a running book whose evaluation cursor missed closed decision bars.

    Different decision clocks never produce false stale alerts: the deadline is
    the oldest missed bar's UTC close plus the documented settling grace, so a
    6h or daily book only alerts once its own missed bar is genuinely overdue.
    Paused books already carry a mismatch alert and are skipped.
    """
    timeframe = deployment.timeframe
    if timeframe is None or deployment.status is not DeploymentStatus.RUNNING:
        return None
    duration = _duration_for(timeframe)
    grace = timedelta(seconds=SETTLING_GRACE_SECONDS)
    due_start = _due_closed_start(now, timeframe)
    cursor = deployment.last_evaluated_bar
    if cursor is None:
        first_close = entry_bar_bucket(deployment.created_at, timeframe) + duration
        if now < first_close + thresholds.decision_missed_bars * duration + grace:
            return None
        missed = thresholds.decision_missed_bars
        cursor_text = "never evaluated"
    else:
        if due_start <= cursor:
            return None
        missed = int((due_start - cursor) / duration)
        if missed < thresholds.decision_missed_bars:
            return None
        cursor_text = cursor.isoformat()
    return SupervisionFinding(
        code=AlertCode.DECISION_DEADLINE_MISSED,
        scope=AlertScope.DEPLOYMENT,
        subject=str(deployment.id),
        severity=AlertSeverity.WARNING,
        detail=(
            f"Running {deployment.mode.value} book on {deployment.product_id} has not "
            f"evaluated its due closed {timeframe} bars: cursor {cursor_text}, {missed} "
            f"bar(s) behind due start {due_start.isoformat()} (settling grace "
            f"{int(SETTLING_GRACE_SECONDS)}s applied to the newest close). Entries stay "
            "fail-closed on stale data."
        ),
        deployment_id=deployment.id,
        product_id=deployment.product_id or None,
    )


def _due_closed_start(now: datetime, timeframe: str) -> datetime:
    """Newest closed bar that is past ADR 0104's publication grace.

    A bar whose close is still inside the 120-second settling window is not due.
    Six-hour and daily clocks therefore do not alert merely because wall time
    crossed their boundary.
    """
    interval = parse_candle_interval(timeframe)
    duration = interval.duration
    newest_start = interval.align_closed_end(now) - duration
    if now < newest_start + duration + timedelta(seconds=SETTLING_GRACE_SECONDS):
        return newest_start - duration
    return newest_start


def _lease_margin(worker_interval_seconds: int) -> timedelta:
    """How long a lease may be expired before supervision treats it as stale.

    Three worker intervals, and at least two 45-second lease TTLs, so a healthy
    renewal between cycles is not an alert. A long configured interval does not
    false-alert a book whose lease simply waits for the next poll.
    """
    return timedelta(seconds=max(3 * worker_interval_seconds, 90))


def _lease_state(deployment: Deployment, *, now: datetime, worker_interval_seconds: int) -> str:
    """Classify lease evidence as fresh, stale, unknown, or too young to judge."""
    margin = _lease_margin(worker_interval_seconds)
    expires_at = deployment.worker_lease_expires_at
    if expires_at is None:
        if now < deployment.created_at + margin:
            return "too_young"
        return "unknown"
    if expires_at > now + timedelta(seconds=WORKER_LEASE_TTL_SECONDS) + margin:
        return "unknown"
    if now > expires_at + margin:
        return "stale"
    return "fresh"


def _lease_stale_finding(
    deployment: Deployment,
    *,
    worker_interval_seconds: int,
    state: str,
) -> SupervisionFinding | None:
    """Alert a running book whose lease is stale or whose lease age is unknown.

    Unknown is not healthy and is not proof the process died. Clock skew between
    the database and this check cannot be excluded, so the detail says so.
    Process liveness (a heartbeat) is a different fact from this book's lease.
    """
    if deployment.status is not DeploymentStatus.RUNNING or state not in {"stale", "unknown"}:
        return None
    margin = _lease_margin(worker_interval_seconds)
    expires_at = deployment.worker_lease_expires_at
    if state == "unknown" and expires_at is not None:
        detail = (
            f"Worker lease expiry {expires_at.isoformat()} for {deployment.mode.value} book "
            f"on {deployment.product_id} is implausibly far in the future for its configured "
            "TTL and poll margin; lease age is unknown and clock skew cannot be excluded. "
            "This is not proof of process death or per-book safety."
        )
    elif expires_at is None:
        detail = (
            f"Running {deployment.mode.value} book on {deployment.product_id} has no "
            f"worker lease (age unknown; grace {int(margin.total_seconds())}s since "
            f"{deployment.created_at.isoformat()}). Unknown is not proof the worker "
            "process is dead and is not proof this book is safe. Clock skew cannot "
            "be excluded."
        )
    else:
        detail = (
            f"Running {deployment.mode.value} book on {deployment.product_id} holds no "
            f"current worker lease (expired {expires_at.isoformat()}, margin "
            f"{int(margin.total_seconds())}s). This book is not being supervised; the "
            "age may include database clock skew. Per-book safety is independent of "
            "process liveness."
        )
    return SupervisionFinding(
        code=AlertCode.WORKER_LEASE_STALE,
        scope=AlertScope.DEPLOYMENT,
        subject=str(deployment.id),
        severity=AlertSeverity.WARNING,
        detail=detail,
        deployment_id=deployment.id,
        product_id=deployment.product_id or None,
    )


def _maintenance_deadline_finding(
    deployment: Deployment, *, state: str
) -> SupervisionFinding | None:
    """Alert when an occupied book has no evidenced protection maintenance.

    This is a lease-timing check, not successful-maintenance telemetry. A fresh
    lease alone cannot prove reconciliation or cover; those use separate checks.
    A stale or unknown lease on an occupied book means stop maintenance may have
    missed its deadline, regardless of process liveness or candle availability.
    """
    if deployment.phase is RuntimePhase.FLAT or state not in {"stale", "unknown"}:
        return None
    if deployment.status not in {
        DeploymentStatus.RUNNING,
        DeploymentStatus.PAUSED,
        DeploymentStatus.STOPPED,
    }:
        return None
    return SupervisionFinding(
        code=AlertCode.MAINTENANCE_DEADLINE_MISSED,
        scope=AlertScope.DEPLOYMENT,
        subject=str(deployment.id),
        severity=(
            AlertSeverity.CRITICAL if deployment.mode.value == "live" else AlertSeverity.WARNING
        ),
        detail=(
            f"Occupied {deployment.mode.value} book on {deployment.product_id} has no "
            f"current worker lease ({state}). Protective maintenance and reconciliation "
            "are not evidenced this interval. This is not an automatic market exit."
        ),
        deployment_id=deployment.id,
        product_id=deployment.product_id or None,
    )


def _duration_for(timeframe: str) -> timedelta:
    """Return one decision-clock bar duration for a stored timeframe value."""
    return parse_candle_interval(timeframe).duration
