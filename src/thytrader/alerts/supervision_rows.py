"""Supervision findings computable from the deployment row alone, and worker book failures.

Covers latched loss and drawdown breakers, portfolio breaker and pause mismatches, the
per-book finding recorded when one worker cycle raised, and the rules that prove a worker
error recovered: an advanced decision cursor, or a stopped book's completed maintenance pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.alerts.models import (
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.trading.models import DeploymentStatus

if TYPE_CHECKING:
    from thytrader.trading.models import (
        Deployment,
    )

#: Portfolio breaker mismatch prefixes the portfolio supervisor writes onto
#: sleeve books (ADR 0091). They are breaker latches, not generic mismatches.
_PORTFOLIO_BREAKER_PREFIXES = ("PORTFOLIO_DRAWDOWN_STOP", "PORTFOLIO_DAILY_LOSS_STOP")


#: The supervision-pause mismatch prefix written by the execution worker when a
#: book fails too many consecutive cycles. Covered by WORKER_BOOK_FAILURES.
_SUPERVISION_PAUSE_PREFIX = "WORKER_CONSECUTIVE_FAILURES"


def verified_worker_recovery(previous: Deployment, current: Deployment) -> bool:
    """Only advancing the same snapshotted decision cursor proves a worker error recovered.

    Generic non-raising passes include lease skips and cache warming. A persisted
    supervision pause retains evidence until manual clearing and verified work.
    """
    old_cursor = previous.last_evaluated_bar
    cursor = current.last_evaluated_bar
    return (
        cursor is not None
        and (old_cursor is None or cursor > old_cursor)
        and current.strategy_fingerprint == previous.strategy_fingerprint
        and current.strategy_id == previous.strategy_id
        and not (current.mismatch_detail or "").startswith(_SUPERVISION_PAUSE_PREFIX)
    )


#: Resolution note for a worker failure on a stopped book proven by its maintenance pass.
STOPPED_MAINTENANCE_RECOVERY = (
    "recovered: a later worker cycle completed this stopped book's maintenance without failure "
    "and found it flat, idle, fully accounted and fault-free"
)


def verified_stopped_maintenance(
    previous: Deployment, current: Deployment, *, maintained: bool
) -> bool:
    """A completed maintenance pass proves a worker error on a stopped book recovered.

    A flat stopped book makes no decisions, so its cursor never advances and the decision rule
    could never clear it. ``maintained`` is the caller's positive evidence: the pass ran to
    completion (not lease-skipped, deferred or warming) and left the book flat, idle, fully
    accounted and fault-free. The book must be stopped before and after the pass, and any
    fail-closed mismatch (a supervision pause, a failed read, a missing broker) keeps it open.
    """
    return (
        maintained
        and previous.status is DeploymentStatus.STOPPED
        and current.status is DeploymentStatus.STOPPED
        and not current.mismatch_detail
    )


_FAILURE_ERROR_MARKER = " Error: "


def worker_book_failure_finding(deployment: Deployment, *, error_type: str) -> SupervisionFinding:
    """Build the per-book finding recorded when one worker cycle raised.

    Only the exception type is recorded; messages can carry provider detail and stay redacted.
    """
    detail = (
        f"Execution cycle failed for {deployment.mode.value} book on {deployment.product_id} "
        f"({deployment.kind.value}); the cycle retries next interval."
        f"{_FAILURE_ERROR_MARKER}{error_type}."
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


def failure_error_type(detail: str) -> str | None:
    """Return the exception type a ``worker_book_failure_finding`` detail recorded, if any."""
    _, marker, tail = detail.rpartition(_FAILURE_ERROR_MARKER)
    error_type = tail.removesuffix(".")
    if not marker or not error_type.isidentifier():
        return None
    return error_type


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
            findings.append(
                SupervisionFinding(
                    code=AlertCode.WORKER_BOOK_FAILURES,
                    scope=AlertScope.DEPLOYMENT,
                    subject=str(deployment.id),
                    severity=AlertSeverity.WARNING,
                    # The pause note carries the last error type; repeat it so re-observing
                    # the pause never replaces the only durable failure evidence.
                    detail=mismatch,
                    deployment_id=deployment.id,
                    product_id=deployment.product_id or None,
                    count_occurrence=False,
                )
            )
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
