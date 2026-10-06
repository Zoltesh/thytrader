"""Per-cycle safety supervision findings for durable operator alerts (ADR 0115).

This module recomputes safety observations from durable execution state on every
worker cycle. It never mutates books, never invents market data, and treats
unknown evidence as "no finding" rather than fabricating health. Findings feed
``thytrader.alerts.service.AlertService``, which deduplicates them into durable
alert rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.alerts.models import (
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.execution.geometry import entry_bar_bucket
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    OrderSide,
    OrderStatus,
    PositionSide,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.execution.protection import book_position_state, book_protection_status
from thytrader.market_data.models import parse_candle_interval

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal
    from uuid import UUID

    from thytrader.execution.models import (
        DeploymentSnapshot,
        Position,
    )
    from thytrader.market_data.models import Candle

_logger = logging.getLogger(__name__)

#: Publication settling grace for decision deadlines, aligned with ADR 0104's
#: fixed 120-second newest-candle wait. A missed-decision alert fires only after
#: the oldest missed bar's close plus this grace has passed, so a late provider
#: publication never produces a false stale alert.
SETTLING_GRACE_SECONDS = 120.0

#: Portfolio breaker mismatch prefixes the portfolio supervisor writes onto
#: sleeve books (ADR 0091). They are breaker latches, not generic mismatches.
_PORTFOLIO_BREAKER_PREFIXES = ("PORTFOLIO_DRAWDOWN_STOP", "PORTFOLIO_DAILY_LOSS_STOP")

#: The supervision-pause mismatch prefix written by the execution worker when a
#: book fails too many consecutive cycles. Covered by WORKER_BOOK_FAILURES.
_SUPERVISION_PAUSE_PREFIX = "WORKER_CONSECUTIVE_FAILURES"

_STOP_TRIGGER_WATCH_STATUSES = frozenset(
    {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN}
)


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    """Explicit, operator-tunable supervision thresholds with sane defaults.

    ``consecutive_failure_cycles`` is how many failing worker cycles in a row
    pause a book's new entries (exits and reconciliation continue).
    ``decision_missed_bars`` is how many closed decision bars behind the venue
    clock a running book may lag before the missed-deadline alert fires; the
    settling grace above still applies per missed bar. ``delivery_max_attempts``
    bounds notification retries for one open alert.
    """

    consecutive_failure_cycles: int = 3
    decision_missed_bars: int = 2
    delivery_max_attempts: int = 5

    def __post_init__(self) -> None:
        """Reject thresholds that would supervise nothing or flood delivery."""
        for name in (
            "consecutive_failure_cycles",
            "decision_missed_bars",
            "delivery_max_attempts",
        ):
            value = getattr(self, name)
            if value < 1:
                message = f"{name} must be at least 1"
                raise ValueError(message)


@runtime_checkable
class DeploymentSnapshotReader(Protocol):
    """Read one deployment with orders and intents for protection classification."""

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment snapshot or fail."""
        ...


@runtime_checkable
class ClosedCandleReader(Protocol):
    """Best-effort closed-candle evidence; failures yield no candles."""

    async def __call__(
        self, product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        """Return the latest closed candles, or an empty tuple when unavailable."""
        ...


async def gather_safety_findings(
    *,
    deployments: Sequence[Deployment],
    snapshots: DeploymentSnapshotReader,
    closed_candles: ClosedCandleReader,
    now: datetime,
    thresholds: AlertThresholds,
    worker_interval_seconds: int,
) -> tuple[SupervisionFinding, ...]:
    """Recompute every safety finding for one supervision pass.

    Reads are best effort: a book whose snapshot or candles cannot be loaded
    produces no finding for that check (never invented evidence), and the
    per-book failure alert is added separately by the worker cycle. Decision
    deadlines use each book's own clock plus the settling grace, including flat
    running books. Lease age is reported even when unknown.
    """
    ordered: dict[tuple[str, str], SupervisionFinding] = {}
    candle_cache: dict[tuple[str, str], tuple[Candle, ...]] = {}
    for deployment in deployments:
        for finding in _row_findings(deployment):
            _keep(ordered, finding)
        _keep(ordered, _decision_deadline_finding(deployment, now=now, thresholds=thresholds))
        lease_state = _lease_state(
            deployment, now=now, worker_interval_seconds=worker_interval_seconds
        )
        _keep(
            ordered,
            _lease_stale_finding(
                deployment, worker_interval_seconds=worker_interval_seconds, state=lease_state
            ),
        )
        _keep(ordered, _maintenance_deadline_finding(deployment, state=lease_state))
        if deployment.phase is RuntimePhase.FLAT:
            continue
        snapshot = await _snapshot_or_none(snapshots, deployment.id)
        if snapshot is None:
            continue
        triggered = await _trigger_consumed_orders(
            deployment, snapshot, closed_candles, candle_cache
        )
        for finding in _protection_findings(deployment, snapshot, triggered):
            _keep(ordered, finding)
        for finding in _stop_trigger_findings(deployment, snapshot, triggered):
            _keep(ordered, finding)
    return tuple(ordered.values())


def _keep(
    ordered: dict[tuple[str, str], SupervisionFinding], finding: SupervisionFinding | None
) -> None:
    """Record the first finding for one identity."""
    if finding is None:
        return
    ordered.setdefault((finding.code.value, finding.subject), finding)


def worker_book_failure_finding(deployment: Deployment, *, error_type: str) -> SupervisionFinding:
    """Build the per-book finding recorded when one worker cycle raised."""
    detail = (
        f"Execution cycle failed for {deployment.mode.value} book on {deployment.product_id} "
        f"({deployment.kind.value}); the cycle retries next interval. Error: {error_type}."
    )
    return SupervisionFinding(
        code=AlertCode.WORKER_BOOK_FAILURES,
        scope=AlertScope.DEPLOYMENT,
        subject=str(deployment.id),
        severity=AlertSeverity.WARNING,
        detail=detail,
        deployment_id=deployment.id,
        product_id=deployment.product_id or None,
    )


def _row_findings(deployment: Deployment) -> tuple[SupervisionFinding, ...]:
    """Findings computable from the deployment row alone."""
    findings: list[SupervisionFinding] = []
    if deployment.daily_loss_latched or deployment.drawdown_latched:
        findings.append(
            SupervisionFinding(
                code=AlertCode.BREAKER_LATCHED,
                scope=AlertScope.DEPLOYMENT,
                subject=str(deployment.id),
                severity=AlertSeverity.CRITICAL,
                detail=_breaker_detail(deployment),
                deployment_id=deployment.id,
                product_id=deployment.product_id or None,
            )
        )
    mismatch = deployment.mismatch_detail or ""
    if mismatch:
        if mismatch.startswith(_SUPERVISION_PAUSE_PREFIX):
            return tuple(findings)
        if mismatch.startswith(_PORTFOLIO_BREAKER_PREFIXES):
            findings.append(
                SupervisionFinding(
                    code=AlertCode.BREAKER_LATCHED,
                    scope=AlertScope.DEPLOYMENT,
                    subject=str(deployment.id),
                    severity=AlertSeverity.CRITICAL,
                    detail=(
                        f"Portfolio breaker holds this {deployment.mode.value} sleeve paused "
                        f"until an operator resets it: {mismatch}"
                    ),
                    deployment_id=deployment.id,
                    product_id=deployment.product_id or None,
                )
            )
            return tuple(findings)
        findings.append(
            SupervisionFinding(
                code=AlertCode.BOOK_PAUSED_MISMATCH,
                scope=AlertScope.DEPLOYMENT,
                subject=str(deployment.id),
                severity=AlertSeverity.WARNING,
                detail=(
                    f"{deployment.mode.value} book is fail-closed paused on "
                    f"{deployment.product_id}: {mismatch}"
                ),
                deployment_id=deployment.id,
                product_id=deployment.product_id or None,
            )
        )
    return tuple(findings)


def _breaker_detail(deployment: Deployment) -> str:
    """Name the latched breakers on one book without balances."""
    latches = []
    if deployment.daily_loss_latched:
        latches.append("daily-loss")
    if deployment.drawdown_latched:
        latches.append("drawdown")
    return (
        f"{deployment.mode.value} book on {deployment.product_id} is latched by its "
        f"{' and '.join(latches)} breaker; entries stay blocked until an operator "
        "resets the latch. Exits and reconciliation continue."
    )


def _protection_findings(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    triggered: Mapping[UUID, Decimal],
) -> tuple[SupervisionFinding, ...]:
    """Alert uncovered or unverifiable exit cover on occupied books.

    A live book whose only resting closing-side stops have all triggered without
    filling no longer has verified cover, even though the venue still lists the
    orders as working, so ``STOP_UNCOVERED`` fires alongside the per-order
    ``STOP_TRIGGERED_UNFILLED`` finding.
    """
    findings: list[SupervisionFinding] = []
    for position in snapshot_positions(snapshot):
        product_id = resolved_product_id(position.product_id, deployment)
        state = book_position_state(
            snapshot, product_id=product_id, position=position, phase=deployment.phase
        )
        status = book_protection_status(snapshot, product_id=product_id, position=position)
        if status.value not in {"unprotected", "unknown"}:
            if not _cover_voided_by_triggered_stops(
                deployment, snapshot, product_id=product_id, position=position, triggered=triggered
            ):
                continue
            findings.append(
                SupervisionFinding(
                    code=AlertCode.STOP_UNCOVERED,
                    scope=AlertScope.DEPLOYMENT,
                    subject=f"{deployment.id}:{product_id}",
                    severity=AlertSeverity.CRITICAL,
                    detail=_trigger_voided_detail(deployment, product_id, position.side.value),
                    deployment_id=deployment.id,
                    product_id=product_id,
                )
            )
            continue
        code = (
            AlertCode.STOP_UNCOVERED
            if status.value == "unprotected"
            else AlertCode.STOP_COVERAGE_UNKNOWN
        )
        severity = (
            AlertSeverity.CRITICAL
            if code is AlertCode.STOP_UNCOVERED and deployment.mode.value == "live"
            else AlertSeverity.WARNING
        )
        findings.append(
            SupervisionFinding(
                code=code,
                scope=AlertScope.DEPLOYMENT,
                subject=f"{deployment.id}:{product_id}",
                severity=severity,
                detail=_uncovered_detail(deployment, position, product_id, state.value),
                deployment_id=deployment.id,
                product_id=product_id,
            )
        )
    return tuple(findings)


def _cover_voided_by_triggered_stops(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position,
    triggered: Mapping[UUID, Decimal],
) -> bool:
    """True when every resting closing-side stop on one product triggered unfilled.

    Only live books lose cover this way: paper stops are enforced synthetically
    on every closed bar regardless of the resting paper order. Any still-working
    closing-side order without a consumed trigger (for example a take-profit)
    keeps the book covered.
    """
    if deployment.mode is not DeploymentMode.LIVE:
        return False
    closing = OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
    watching = tuple(
        order
        for order in snapshot.orders
        if resolved_product_id(order.product_id, deployment) == product_id
        and order.side is closing
        and order.status in _STOP_TRIGGER_WATCH_STATUSES
    )
    if not watching:
        return False
    return all(order.id in triggered for order in watching)


def _trigger_voided_detail(deployment: Deployment, product_id: str, side: str) -> str:
    """Describe cover lost to a triggered-but-unfilled stop without quantities."""
    return (
        f"Open {deployment.mode.value} book on {product_id} (side {side}) has no verified "
        "resting exit: its protective stop triggered on the latest closed bar without "
        "filling. Risk-reducing exits are not blocked; new entries are."
    )


def _uncovered_detail(
    deployment: Deployment, position: Position, product_id: str, state: str
) -> str:
    """Describe missing cover without quantities or prices."""
    mode = deployment.mode.value
    if state == "unknown":
        return (
            f"Open {mode} book on {product_id} has protective orders whose venue state is "
            "unreconciled; cover cannot be verified. Reconciliation continues; treat as "
            "unverified, not safe."
        )
    return (
        f"Open {mode} book on {product_id} (side {position.side.value}) has no verified "
        "resting exit: no attached bracket child and no working protective order. "
        "Risk-reducing exits are not blocked; new entries are."
    )


async def _trigger_consumed_orders(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    closed_candles: ClosedCandleReader,
    candle_cache: dict[tuple[str, str], tuple[Candle, ...]],
) -> dict[UUID, Decimal]:
    """Map each protective order whose stop trigger traded to the latest close.

    A bar that closed before the order existed never consumes its trigger, and
    unavailable candles consume nothing (no invented market data).
    """
    consumed: dict[UUID, Decimal] = {}
    timeframe = deployment.timeframe
    if timeframe is None:
        return consumed
    for order in snapshot.orders:
        if order.status not in _STOP_TRIGGER_WATCH_STATUSES:
            continue
        trigger = order.stop_trigger_price
        if trigger is None or order.filled_quantity >= order.quantity:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        candles = await _candles_for(
            closed_candles, candle_cache, product_id, timeframe, deployment.created_at
        )
        latest = candles[-1] if candles else None
        if latest is None or latest.starts_at + _duration_for(timeframe) <= order.created_at:
            continue
        if latest.low <= trigger if order.side is OrderSide.SELL else latest.high >= trigger:
            consumed[order.id] = latest.close
    return consumed


def _stop_trigger_findings(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    triggered: Mapping[UUID, Decimal],
) -> tuple[SupervisionFinding, ...]:
    """Alert a resting protective order whose stop trigger traded through but did not fill."""
    findings: list[SupervisionFinding] = []
    timeframe = deployment.timeframe
    if timeframe is None or not triggered:
        return ()
    for order in snapshot.orders:
        latest_close = triggered.get(order.id)
        if latest_close is None:
            continue
        trigger = order.stop_trigger_price
        if trigger is None:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        severity = (
            AlertSeverity.CRITICAL if deployment.mode.value == "live" else AlertSeverity.WARNING
        )
        findings.append(
            SupervisionFinding(
                code=AlertCode.STOP_TRIGGERED_UNFILLED,
                scope=AlertScope.DEPLOYMENT,
                subject=f"{deployment.id}:{order.id}",
                severity=severity,
                detail=(
                    f"Protective {order.side.value} stop on {product_id} triggered on the "
                    f"latest closed {timeframe} bar (trigger {trigger}, last close "
                    f"{latest_close}, filled {order.filled_quantity} of {order.quantity}) but "
                    "remains unfilled. Supervision does not escalate to market orders; "
                    "operator review required."
                ),
                deployment_id=deployment.id,
                product_id=product_id,
            )
        )
    return tuple(findings)


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
    if state == "unknown" or expires_at is None:
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

    Maintenance is the leased reconcile/protection pass, not a successful signal
    evaluation. A fresh lease means that pass is current even if the decision
    cursor is waiting on a settling bar. A stale or unknown lease on an open
    book means stop maintenance may have missed its deadline.
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


async def _snapshot_or_none(
    snapshots: DeploymentSnapshotReader, deployment_id: UUID
) -> DeploymentSnapshot | None:
    """Read one snapshot, logging and yielding nothing on failure."""
    try:
        return await snapshots.get_deployment(deployment_id)
    except Exception as error:  # noqa: BLE001 - best-effort supervision evidence only.
        _logger.warning(
            "alert_snapshot_unavailable deployment_id=%s type=%s",
            deployment_id,
            type(error).__name__,
        )
        return None


async def _candles_for(
    closed_candles: ClosedCandleReader,
    candle_cache: dict[tuple[str, str], tuple[Candle, ...]],
    product_id: str,
    timeframe: str,
    deploy_anchor: datetime,
) -> tuple[Candle, ...]:
    """Read cached closed candles for one product clock; empty on failure."""
    key = (product_id, timeframe)
    if key in candle_cache:
        return candle_cache[key]
    try:
        candles = await closed_candles(product_id, timeframe, deploy_anchor)
    except Exception as error:  # noqa: BLE001 - best-effort supervision evidence only.
        _logger.warning(
            "alert_candles_unavailable product_id=%s timeframe=%s type=%s",
            product_id,
            timeframe,
            type(error).__name__,
        )
        candles = ()
    candle_cache[key] = candles
    return candles


def _duration_for(timeframe: str) -> timedelta:
    """Return one decision-clock bar duration for a stored timeframe value."""
    return parse_candle_interval(timeframe).duration
