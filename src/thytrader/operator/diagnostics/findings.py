"""Risk, reconciliation, and monitor findings operator reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.audit_events import AuditEventUnavailableError
from thytrader.execution.entry import split_pending_entry
from thytrader.execution.reconcile import FILLED_WITHOUT_REST_FILLS_DETAIL
from thytrader.memory.service import build_monitor, storage_label
from thytrader.memory.store import DisabledExperientialMemoryStore
from thytrader.operator.audit_findings import audit_failure_findings
from thytrader.operator.models import STANDARD_REDACTION, ComponentReport, ReportStatus
from thytrader.operator.runtime_models import (
    MonitorReport,
    ReconciliationFinding,
    ReconciliationPayload,
    ReconciliationReport,
    RiskFinding,
    RiskPayload,
    RiskReport,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.risk.models import RiskPolicySource
from thytrader.risk.store import load_effective_policy
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.memory.models import MonitorSnapshot
    from thytrader.operator.service import OperatorDiagnostics


_BREAKER_FINDING_CODES = {"DAILY_LOSS_LIMIT", "STRATEGY_DRAWDOWN_LIMIT"}


async def build_risk_report(diagnostics: OperatorDiagnostics) -> RiskReport:
    """Surface pause/mismatch findings and the effective risk-policy registry."""
    now = datetime.now(UTC)
    findings, components = await _risk_findings(diagnostics)
    active = await load_effective_policy(diagnostics.risk_policies)
    deployments = await diagnostics.execution.list_deployments()
    paper_running, paper_open = _mode_slot_counts(deployments, DeploymentMode.PAPER)
    live_running, live_open = _mode_slot_counts(deployments, DeploymentMode.LIVE)
    policy = active.definition
    if live_running > 0 and active.source is RiskPolicySource.COMPILED_DEFAULT:
        # New live starts require a published policy (audit F25); a running live
        # deployment under the compiled default can only predate that gate.
        findings = (
            *findings,
            RiskFinding(
                reason_code="LIVE_RUNNING_ON_COMPILED_DEFAULT_POLICY",
                deployment_id=None,
                detail=(
                    "A live deployment is running under the compiled default risk "
                    "policy. Publish an explicit policy for this operator's capital "
                    "and product universe."
                ),
            ),
        )
    components = [
        ComponentReport(
            name="risk_policy_registry",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"Active risk policy {active.policy_fingerprint} ({active.source.value}).",
        ),
        *components,
    ]
    return RiskReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=RiskPayload(
            risk_policy_registry="available",
            policy_source=active.source.value,
            policy_fingerprint=active.policy_fingerprint,
            max_concurrent_running_deployments=policy.max_concurrent_running_deployments,
            max_concurrent_open_positions=policy.max_concurrent_open_positions,
            product_allowlist=policy.product_allowlist,
            paper_running_deployments=paper_running,
            live_running_deployments=live_running,
            paper_open_positions=paper_open,
            live_open_positions=live_open,
            daily_loss_limit_fraction=policy.daily_loss_limit_fraction,
            max_strategy_drawdown_fraction=policy.max_strategy_drawdown_fraction,
            max_entry_orders_per_minute=policy.max_entry_orders_per_minute,
            max_cancellations_per_minute=policy.max_cancellations_per_minute,
            reference_price_collar_fraction=policy.reference_price_collar_fraction,
            allow_intra_strategy_pyramiding=policy.allow_intra_strategy_pyramiding,
            max_daily_loss_quote=policy.max_daily_loss_quote,
            max_portfolio_exposure_quote=policy.max_portfolio_exposure_quote,
            max_venue_order_actions_per_minute=policy.max_venue_order_actions_per_minute,
            max_order_quantity=policy.max_order_quantity,
            max_order_notional_quote=policy.max_order_notional_quote,
            min_available_quote_reserve=policy.min_available_quote_reserve,
            findings=findings,
        ),
    )


async def build_reconciliation_report(diagnostics: OperatorDiagnostics) -> ReconciliationReport:
    """List mismatch, unknown-order, and recent audit failure conditions."""
    now = datetime.now(UTC)
    findings, components, warnings = await _reconciliation_findings(diagnostics)
    if not components:
        components.append(
            ComponentReport(
                name="reconciliation",
                status=ReportStatus.HEALTHY,
                reason_code="OK",
                detail="No mismatch or unknown-order findings were reported.",
            )
        )
    return ReconciliationReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=ReconciliationPayload(findings=findings),
    )


async def build_monitor_report(diagnostics: OperatorDiagnostics) -> MonitorReport:
    """Watch deployments, recent journals, and notification delivery."""
    now = datetime.now(UTC)
    store = diagnostics.memory_store or DisabledExperientialMemoryStore()
    snapshot = await build_monitor(
        store,
        diagnostics.execution,
        diagnostics.settings,
        storage=storage_label(store),
    )
    components = _monitor_components(snapshot)
    warnings: list[str] = []
    if snapshot.memory.storage == "unavailable":
        warnings.append("Experiential memory storage is unavailable; journal writes fail closed.")
    return MonitorReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=snapshot,
    )


async def _risk_findings(
    diagnostics: OperatorDiagnostics,
) -> tuple[tuple[RiskFinding, ...], list[ComponentReport]]:
    """Collect pause, breaker, and mismatch observations from deployments."""
    components: list[ComponentReport] = []
    findings: list[RiskFinding] = []
    deployments = await diagnostics.execution.list_deployments()
    for deployment in deployments:
        findings.extend(_deployment_risk_findings(deployment))
    if findings:
        components.append(
            ComponentReport(
                name="runtime_risk",
                status=ReportStatus.DEGRADED,
                reason_code="FINDINGS_PRESENT",
                detail="One or more deployments are paused, mismatched, or breaker-tripped.",
            )
        )
    return tuple(findings), components


async def _reconciliation_findings(
    diagnostics: OperatorDiagnostics,
) -> tuple[tuple[ReconciliationFinding, ...], list[ComponentReport], list[str]]:
    """Inspect deployments and recent audit failures without mutating state."""
    components: list[ComponentReport] = []
    warnings: list[str] = []
    findings: list[ReconciliationFinding] = []
    deployments = await diagnostics.execution.list_deployments()
    for deployment in deployments:
        if deployment.mismatch_detail:
            findings.append(
                ReconciliationFinding(
                    reason_code="STATE_MISMATCH",
                    deployment_id=deployment.id,
                    detail=deployment.mismatch_detail[:500],
                )
            )
            findings.extend(_precise_mismatch_findings(deployment))
        if deployment.status is not DeploymentStatus.STOPPED:
            await _collect_unknown_orders(diagnostics, deployment, findings)
    findings = _dedupe_findings(findings)
    try:
        events = await diagnostics.audit.list_recent(limit=20)
    except AuditEventUnavailableError:
        warnings.append("Audit events are unavailable; reconciliation is partial.")
        components.append(
            ComponentReport(
                name="audit",
                status=ReportStatus.DEGRADED,
                reason_code="AUDIT_UNAVAILABLE",
                detail="Recent audit events could not be listed.",
            )
        )
    else:
        findings.extend(audit_failure_findings(events))
    if findings:
        components.append(
            ComponentReport(
                name="reconciliation",
                status=ReportStatus.DEGRADED,
                reason_code="FINDINGS_PRESENT",
                detail="Mismatch, unknown orders, or audit failures were reported.",
            )
        )
    return tuple(findings), components, warnings


async def _collect_unknown_orders(
    diagnostics: OperatorDiagnostics,
    deployment: Deployment,
    findings: list[ReconciliationFinding],
) -> None:
    """Append unknown-order findings for one active deployment."""
    try:
        snapshot = await diagnostics.execution.get_deployment(deployment.id)
    except Exception:  # noqa: BLE001 - snapshot failures stay partial.
        findings.append(
            ReconciliationFinding(
                reason_code="SNAPSHOT_UNAVAILABLE",
                deployment_id=deployment.id,
                detail="Open orders could not be loaded for reconciliation.",
            )
        )
        return
    unknown = [order for order in snapshot.orders if order.status is OrderStatus.UNKNOWN]
    if unknown:
        findings.append(
            ReconciliationFinding(
                reason_code="UNKNOWN_ORDERS",
                deployment_id=deployment.id,
                detail=f"{len(unknown)} order(s) remain in unknown status.",
            )
        )
    _collect_split_pending_entry(deployment, snapshot, findings)


def _collect_split_pending_entry(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    findings: list[ReconciliationFinding],
) -> None:
    """Flag pending-entry books whose working order or position is missing.

    A FILLED order with no applied fill row and no position means fill
    economics never committed (ADR 0057 violation). A PENDING_ENTRY book
    with no working entry and no position means the entry lifecycle was
    skipped. Both demand operator attention; neither is inventoried.
    """
    if not split_pending_entry(snapshot):
        return
    findings.append(
        ReconciliationFinding(
            reason_code=(
                "FILLED_WITHOUT_FILL"
                if any(order.status is OrderStatus.FILLED for order in snapshot.orders)
                else "PENDING_ENTRY_WITHOUT_ENTRY"
            ),
            deployment_id=deployment.id,
            detail=(
                "Pending-entry book has no working order and no position; "
                "entry state is split and the runtime will fail closed."
            ),
        )
    )


def _precise_mismatch_findings(deployment: Deployment) -> tuple[ReconciliationFinding, ...]:
    """Add a precise code alongside STATE_MISMATCH for known live reconcile pauses.

    A live order Coinbase reports FILLED while List Fills returns no rows is the
    live form of ``FILLED_WITHOUT_FILL``: no fill economics were applied.
    """
    if deployment.mismatch_detail == FILLED_WITHOUT_REST_FILLS_DETAIL:
        return (
            ReconciliationFinding(
                reason_code="FILLED_WITHOUT_FILL",
                deployment_id=deployment.id,
                detail=(
                    "Venue reports a FILLED order but Coinbase List Fills returned no rows; "
                    "no fill economics were applied and the book is paused."
                ),
            ),
        )
    return ()


def _dedupe_findings(
    findings: list[ReconciliationFinding],
) -> list[ReconciliationFinding]:
    """Keep the first finding per (reason_code, deployment_id), preserving order."""
    seen: set[tuple[str, UUID | None]] = set()
    unique: list[ReconciliationFinding] = []
    for finding in findings:
        key = (finding.reason_code, finding.deployment_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _monitor_components(snapshot: MonitorSnapshot) -> list[ComponentReport]:
    """Map monitor findings to operator components without treating default-off notify as failed."""
    components: list[ComponentReport] = []
    if snapshot.memory.storage == "unavailable":
        components.append(
            ComponentReport(
                name="memory_storage",
                status=ReportStatus.FAILED,
                reason_code="MEMORY_STORAGE_UNAVAILABLE",
                detail="Experiential memory has no durable store; journal writes fail closed.",
            )
        )
    else:
        components.append(
            ComponentReport(
                name="memory_storage",
                status=ReportStatus.HEALTHY,
                reason_code="OK",
                detail="Experiential memory store is available.",
            )
        )
    failed_codes = {
        "MEMORY_STORAGE_UNAVAILABLE",
        "EXECUTION_UNAVAILABLE",
        "NOTIFICATION_FAILED",
    }
    for finding in snapshot.findings:
        if finding.reason_code == "MEMORY_STORAGE_UNAVAILABLE":
            continue
        components.append(
            ComponentReport(
                name="monitor",
                status=(
                    ReportStatus.FAILED
                    if finding.reason_code in failed_codes
                    else ReportStatus.DEGRADED
                ),
                reason_code=finding.reason_code,
                detail=finding.detail,
            )
        )
    return components


def _deployment_risk_findings(deployment: Deployment) -> tuple[RiskFinding, ...]:
    """Emit pause, mismatch, and breaker-trip findings for one deployment."""
    findings: list[RiskFinding] = []
    if deployment.status is DeploymentStatus.PAUSED:
        findings.append(
            RiskFinding(
                reason_code="DEPLOYMENT_PAUSED",
                deployment_id=deployment.id,
                detail=deployment.mismatch_detail or "Operator or runtime pause is in effect.",
            )
        )
    if deployment.daily_loss_latched:
        findings.append(
            RiskFinding(
                reason_code="DAILY_LOSS_LIMIT",
                deployment_id=deployment.id,
                detail="Daily-loss breaker is latched until an explicit operator reset.",
            )
        )
    if deployment.drawdown_latched:
        findings.append(
            RiskFinding(
                reason_code="STRATEGY_DRAWDOWN_LIMIT",
                deployment_id=deployment.id,
                detail="Drawdown breaker is latched until an explicit operator reset.",
            )
        )
    detail = deployment.mismatch_detail
    if not detail:
        return tuple(findings)
    findings.append(
        RiskFinding(
            reason_code="STATE_MISMATCH",
            deployment_id=deployment.id,
            detail=detail[:500],
        )
    )
    prefix = detail.split(":", 1)[0]
    if prefix in _BREAKER_FINDING_CODES:
        findings.append(
            RiskFinding(reason_code=prefix, deployment_id=deployment.id, detail=detail[:500])
        )
    return tuple(findings)


def _mode_slot_counts(deployments: tuple[Deployment, ...], mode: DeploymentMode) -> tuple[int, int]:
    """Count occupied running slots and in-market open slots for one mode."""
    occupied = tuple(
        item
        for item in deployments
        if item.mode is mode and item.status in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
    )
    open_count = sum(
        1
        for item in occupied
        if item.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}
    )
    return len(occupied), open_count
