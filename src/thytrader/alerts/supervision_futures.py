"""Read-only futures safety evidence; missing observations never certify recovery.

These findings grant no order, cancellation, lifecycle, or risk-policy authority. The
existing alert store owns dedupe, restart watermarks, resolution and notification delivery.
"""

from __future__ import annotations

from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING, Literal

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SafetyEvidence,
    SupervisionFinding,
)
from thytrader.execution.futures_live import managed_live_futures
from thytrader.execution.futures_visibility import visible_position_comparison
from thytrader.trading.models import snapshot_positions

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot

ACCOUNT_SUBJECT = "futures:live"


def futures_safety_evidence(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    account: FuturesLiveAccount | None,
    policy: RiskPolicyDefinition | None,
    now: datetime,
) -> SafetyEvidence:
    """Observe a complete inventory; stale/future/missing financial evidence stays unknown.

    A durable de-risk duty is itself positive evidence even if the newest venue read
    fails. Unknown margin cannot clear a prior low-margin condition. Paper never enters
    the live account scope. A threshold of zero on a held account is unknown, not safe.
    """
    books = tuple(book for book in snapshots if managed_live_futures(book))
    held = any(snapshot_positions(book) for book in books)
    findings: list[SupervisionFinding] = []
    evaluated: list[AlertCheck] = []
    known = (
        account is not None and account.venue.is_fresh(now) and _margin_known(account, held=held)
    )
    if held and not known:
        findings.append(
            _account_finding(
                AlertCode.FUTURES_ACCOUNT_UNKNOWN,
                AlertSeverity.CRITICAL,
                "Managed live futures inventory has unknown, stale or future-dated CFM margin "
                "evidence; the margin monitor is blind. Unknown is not zero.",
            )
        )
    elif known or not books:
        evaluated.append(AlertCheck(AlertCode.FUTURES_ACCOUNT_UNKNOWN, ACCOUNT_SUBJECT))
    pending = any(book.deployment.futures_derisk_pending for book in books)
    confirmed_flat = (
        bool(books)
        and not held
        and all(
            (sample := visible_position_comparison(book, account, now=now)) is not None
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
        evidence = _book_evidence(book, account, now)
        findings.extend(evidence.findings)
        evaluated.extend(evidence.evaluated)
    return SafetyEvidence(tuple(findings), tuple(evaluated))


def _book_evidence(
    book: DeploymentSnapshot,
    account: FuturesLiveAccount | None,
    now: datetime,
) -> SafetyEvidence:
    """Persisted I9 pauses and complete external-order facts; never infer excess ownership."""
    findings: list[SupervisionFinding] = []
    evaluated: list[AlertCheck] = []
    deployment = book.deployment
    venue = None if account is None else account.venue
    sample = visible_position_comparison(book, account, now=now)
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
    if ratio is None or available is None or threshold is None:
        return None
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            critical = threshold * Decimal(ratio)
            warning = critical * 2
            if not critical.is_finite() or critical <= 0 or not warning.is_finite():
                return None
            if available <= critical:
                return AlertSeverity.CRITICAL
            if available < warning:
                return AlertSeverity.WARNING
            return "healthy"
    except DecimalException:
        return None


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
