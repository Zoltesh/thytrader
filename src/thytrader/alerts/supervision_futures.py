"""Read-only futures safety evidence; missing observations never certify recovery.

These findings grant no order, cancellation, lifecycle, or risk-policy authority. The
existing alert store owns dedupe, restart watermarks, resolution and notification delivery.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SafetyEvidence,
    SupervisionFinding,
)
from thytrader.audit_events import AuditEventCategory, AuditEventOutcome
from thytrader.execution.futures_live import managed_live_futures
from thytrader.execution.futures_visibility import visible_position_comparison
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.futures_margin_evidence import margin_state
from thytrader.trading.models import DeploymentMode, snapshot_positions

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.audit_events import AuditEvent
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot

ACCOUNT_SUBJECT = "futures:live"


def orphan_finding(event: AuditEvent, *, now: datetime) -> SupervisionFinding | None:
    """One-off cancellation notification keyed to its durable audit event, not a new cancel."""
    if (
        event.action != "futures_orphan_order_cancelled"
        or event.category is not AuditEventCategory.RUNTIME
        or event.outcome is not AuditEventOutcome.INFO
        or event.occurred_at > now
        or event.product_id is None
        or not is_futures_product_id(event.product_id)
    ):
        return None
    return SupervisionFinding(
        code=AlertCode.FUTURES_ORPHAN_ORDER,
        scope=AlertScope.FLEET,
        subject=f"orphan:{event.id}",
        severity=AlertSeverity.WARNING,
        product_id=event.product_id,
        detail=(
            "A managed protective order on a flat futures book was confirmed "
            "cancelled; one-off audit observation, no additional control action."
        ),
    )


def futures_safety_evidence(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    account: FuturesLiveAccount | None,
    policy: RiskPolicyDefinition | None,
    now: datetime,
    observation_failed: bool = False,
) -> SafetyEvidence:
    """Observe a complete inventory; stale/future/missing financial evidence stays unknown.

    A durable de-risk duty is itself positive evidence even if the newest venue read
    fails. Unknown margin cannot clear a prior low-margin condition. Paper never enters
    the live account scope. A threshold of zero on a held account is unknown, not safe.
    """
    live = tuple(
        book
        for book in snapshots
        if book.deployment.mode is DeploymentMode.LIVE
        and is_futures_product_id(book.deployment.product_id)
    )
    if any(not book.accounting_complete for book in live):
        return SafetyEvidence(
            (
                _account_finding(
                    AlertCode.FUTURES_ACCOUNT_UNKNOWN,
                    AlertSeverity.CRITICAL,
                    "Required futures accounting inventory is incomplete; "
                    "managed scope and recovery are unknown.",
                ),
            ),
            (),
        )
    books = tuple(book for book in live if managed_live_futures(book))
    held = any(snapshot_positions(book) for book in books)
    findings: list[SupervisionFinding] = []
    evaluated: list[AlertCheck] = []
    known = (
        account is not None and account.venue.is_fresh(now) and _margin_known(account, held=held)
    )
    if books and (observation_failed or (not known and _observation_overdue(books, account, now))):
        findings.append(
            _account_finding(
                AlertCode.FUTURES_ACCOUNT_UNKNOWN,
                AlertSeverity.CRITICAL,
                "Managed live futures inventory has unknown, stale or future-dated CFM margin "
                "evidence; the margin monitor is blind. Unknown is not zero.",
            )
        )
    elif (
        known
        and all(
            (sample := visible_position_comparison(book, account, books=books, now=now)) is not None
            and sample.matches
            for book in books
        )
    ) or not books:
        evaluated.append(AlertCheck(AlertCode.FUTURES_ACCOUNT_UNKNOWN, ACCOUNT_SUBJECT))
    pending = any(book.deployment.futures_derisk_pending for book in books)
    confirmed_flat = (
        bool(books)
        and not held
        and all(
            (sample := visible_position_comparison(book, account, books=books, now=now)) is not None
            and sample.matches
            and sample.book_contracts == 0
            for book in books
        )
    )
    if not pending and (not books or (known and confirmed_flat)):
        evaluated.append(AlertCheck(AlertCode.FUTURES_MARGIN_LOW, ACCOUNT_SUBJECT))
    severity = None
    if pending:
        severity = AlertSeverity.CRITICAL
    elif held and known and account is not None:
        severity = _margin_severity(account, policy)
        if severity == "healthy":
            evaluated.append(AlertCheck(AlertCode.FUTURES_MARGIN_LOW, ACCOUNT_SUBJECT))
    if isinstance(severity, AlertSeverity):
        findings.append(
            _account_finding(
                AlertCode.FUTURES_MARGIN_LOW,
                severity,
                "Managed live futures margin is below its warning boundary or at/below its "
                "de-risk threshold, or a durable de-risk obligation is pending.",
            )
        )
    for book in books:
        evidence = _book_evidence(book, account, now, books)
        findings.extend(evidence.findings)
        evaluated.extend(evidence.evaluated)
    return SafetyEvidence(tuple(findings), tuple(evaluated))


def _book_evidence(
    book: DeploymentSnapshot,
    account: FuturesLiveAccount | None,
    now: datetime,
    books: Sequence[DeploymentSnapshot],
) -> SafetyEvidence:
    """Persisted I9 pauses and complete external-order facts; never infer excess ownership."""
    findings: list[SupervisionFinding] = []
    evaluated: list[AlertCheck] = []
    deployment = book.deployment
    venue = None if account is None else account.venue
    sample = visible_position_comparison(book, account, books=books, now=now)
    subject = f"deployment:{deployment.id}"
    mismatch = AlertCode.FUTURES_POSITION_MISMATCH
    if sample is not None and sample.matches:
        evaluated.append(AlertCheck(mismatch, subject))
    elif (deployment.mismatch_detail or "").startswith(mismatch.value):
        findings.append(
            SupervisionFinding(
                code=mismatch,
                scope=AlertScope.DEPLOYMENT,
                subject=subject,
                severity=AlertSeverity.CRITICAL,
                deployment_id=deployment.id,
                product_id=deployment.product_id,
                detail=deployment.mismatch_detail or mismatch.value,
            )
        )
    external = AlertCode.FUTURES_EXTERNAL_ON_MANAGED_PRODUCT
    if venue is not None and venue.is_fresh(now) and venue.external_order_products is not None:
        product = deployment.product_id
        if product in venue.external_order_products:
            findings.append(
                SupervisionFinding(
                    code=external,
                    scope=AlertScope.FLEET,
                    subject=f"product:{product}",
                    severity=AlertSeverity.CRITICAL,
                    deployment_id=deployment.id,
                    product_id=product,
                    detail="An external futures order occupies a managed product.",
                )
            )
        elif sample is not None and sample.matches:
            evaluated.append(AlertCheck(external, f"product:{product}"))
    return SafetyEvidence(tuple(findings), tuple(evaluated))


def _observation_overdue(
    books: Sequence[DeploymentSnapshot], account: FuturesLiveAccount | None, now: datetime
) -> bool:
    """A failed read is immediate; unobserved/pending evidence has a three-minute grace.

    Use the last received observation when present, never a local refresh timestamp.
    Without one, the earliest durable risk obligation is a conservative lower bound.
    """
    if account is not None:
        return not account.venue.is_fresh(now) or not _margin_known(account, held=True)
    anchors = [
        book.deployment.futures_flat_confirmation_after or book.deployment.created_at
        for book in books
    ]
    return any(stamp > now or now - stamp > timedelta(seconds=180) for stamp in anchors)


def _margin_known(account: FuturesLiveAccount, *, held: bool) -> bool:
    """Both amounts and a complete position listing are needed to supervise margin."""
    available = account.available_margin_usd
    threshold = account.liquidation_threshold_usd
    return (
        account.venue.positions is not None
        and available is not None
        and available.is_finite()
        and threshold is not None
        and threshold.is_finite()
        and (threshold > 0 if held else threshold >= 0)
    )


def _margin_severity(
    account: FuturesLiveAccount,
    policy: RiskPolicyDefinition | None,
) -> AlertSeverity | Literal["healthy"] | None:
    """Compare exact products instead of a rounded margin ratio, under caller traps."""
    futures = None if policy is None else policy.futures
    ratio = None if futures is None else futures.live_derisk_margin_ratio
    available = account.available_margin_usd
    threshold = account.liquidation_threshold_usd
    state = margin_state(available, threshold, ratio)
    if state == "critical":
        return AlertSeverity.CRITICAL
    if state == "warning":
        return AlertSeverity.WARNING
    return state


def _account_finding(
    code: AlertCode,
    severity: AlertSeverity,
    detail: str,
) -> SupervisionFinding:
    """A stable account-scoped alert, delivered by the existing notification path."""
    return SupervisionFinding(
        code=code,
        scope=AlertScope.FLEET,
        subject=ACCOUNT_SUBJECT,
        severity=severity,
        detail=detail,
    )
