"""Operator ``fleet-health`` report and the ``health`` fleet component (ADR 0130).

``fleet-health`` answers "can the fleet enter at all, and if not, exactly why?": a fresh
fleet entry readiness evaluation (``operator.fleet_entries``), the 24 h decision-log
aggregation of running books (``operator.fleet_decisions``) and the open
``FLEET_ENTRIES_BLOCKED`` alerts the execution worker raised.

``health`` stays cheap: its ``fleet_entries`` component reads those open alerts, which the
worker re-evaluates every cycle with the gate's own evidence. A critical one (a live scope
blocked by missing evidence) fails health; any other degrades it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.alerts.models import AlertCode, AlertSeverity
from thytrader.alerts.store import DisabledAlertStore
from thytrader.operator.fleet_decisions import fleet_decision_log
from thytrader.operator.fleet_entries import (
    FLEET_COMPONENT,
    fleet_entries_component,
    fleet_entries_payload,
)
from thytrader.operator.fleet_health_models import (
    FleetAlertPayload,
    FleetDecisionLogPayload,
    FleetHealthPayload,
    FleetHealthReport,
)
from thytrader.operator.models import STANDARD_REDACTION, ComponentReport, ReportStatus
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from thytrader.alerts.models import OperatorAlert
    from thytrader.alerts.store import AlertStore
    from thytrader.operator.service import OperatorDiagnostics


async def build_fleet_health_report(diagnostics: OperatorDiagnostics) -> FleetHealthReport:
    """Evaluate fleet entry readiness, aggregate recent decisions and list fleet alerts."""
    now = datetime.now(UTC)
    entries = await fleet_entries_payload(
        diagnostics.execution,
        diagnostics.risk_policies,
        market_data=diagnostics.market_data,
        futures_account=diagnostics.futures_account_store,
    )
    try:
        deployments = await diagnostics.execution.list_deployments()
    except Exception:  # noqa: BLE001 - an unreadable fleet leaves the decision log unread.
        deployments = ()
    decisions = await fleet_decision_log(diagnostics.decision_store, deployments, now=now)
    alerts, storage = await open_fleet_alerts(diagnostics.alert_store)
    components = [
        fleet_entries_component(entries),
        _decisions_component(decisions),
    ]
    warnings: list[str] = []
    if decisions.truncated:
        warnings.append(
            "Some running books have more blocked bars than one read; counts are a floor."
        )
    if storage == "unavailable":
        warnings.append("Alert storage is unavailable; open fleet alerts are not listed.")
    return FleetHealthReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=FleetHealthPayload(
            entries=entries,
            decisions=decisions,
            alert_storage=storage,
            open_fleet_alerts=tuple(_alert_payload(row) for row in alerts),
        ),
    )


async def open_fleet_alerts(
    store: AlertStore | None,
) -> tuple[tuple[OperatorAlert, ...], Literal["available", "unavailable"]]:
    """Open ``FLEET_ENTRIES_BLOCKED`` rows, or unavailable when the feed cannot be read."""
    if store is None or isinstance(store, DisabledAlertStore):
        return (), "unavailable"
    try:
        rows = await store.list_open_alerts()
    except Exception:  # noqa: BLE001 - a read failure is an unavailable feed, not an empty one.
        return (), "unavailable"
    fleet = tuple(row for row in rows if row.code is AlertCode.FLEET_ENTRIES_BLOCKED)
    return tuple(sorted(fleet, key=lambda row: row.subject)), "available"


async def fleet_alert_health_component(store: AlertStore | None) -> ComponentReport:
    """Health's fleet component, from the worker's open fleet entry alerts."""
    rows, storage = await open_fleet_alerts(store)
    if storage == "unavailable":
        return ComponentReport(
            name=FLEET_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="FLEET_ENTRY_ALERTS_UNAVAILABLE",
            detail="The alert feed cannot be read, so a fleet-wide entry block would be unseen.",
        )
    if not rows:
        return ComponentReport(
            name=FLEET_COMPONENT,
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail="No open fleet entry block alert.",
        )
    critical = any(row.severity is AlertSeverity.CRITICAL for row in rows)
    subjects = ", ".join(row.subject for row in rows)
    return ComponentReport(
        name=FLEET_COMPONENT,
        status=ReportStatus.FAILED if critical else ReportStatus.DEGRADED,
        reason_code="FLEET_ENTRIES_BLOCKED",
        detail=f"New entries are blocked fleet-wide in {subjects}: {rows[0].detail}"[:500],
    )


def _decisions_component(decisions: FleetDecisionLogPayload) -> ComponentReport:
    """Degrade on systemic decision-log blockers or an unreadable journal."""
    if decisions.storage == "unavailable":
        return ComponentReport(
            name="fleet_decisions",
            status=ReportStatus.DEGRADED,
            reason_code="DECISION_STORAGE_UNAVAILABLE",
            detail="The decision journal cannot be read; recent blocked bars are unknown.",
        )
    if decisions.unreadable_deployment_ids:
        return ComponentReport(
            name="fleet_decisions",
            status=ReportStatus.DEGRADED,
            reason_code="DECISION_JOURNAL_READ_FAILED",
            detail=f"{len(decisions.unreadable_deployment_ids)} running book(s) were unreadable.",
        )
    if decisions.systemic:
        kinds = sorted({item.kind for item in decisions.systemic})
        codes = sorted({item.reason_code for item in decisions.systemic})
        return ComponentReport(
            name="fleet_decisions",
            status=ReportStatus.DEGRADED,
            reason_code="SYSTEMIC_ENTRY_BLOCKERS",
            detail=f"Systemic {', '.join(kinds)} in the last "
            f"{decisions.window_hours} h: {', '.join(codes)}."[:500],
        )
    return ComponentReport(
        name="fleet_decisions",
        status=ReportStatus.HEALTHY,
        reason_code="OK",
        detail=f"No systemic blocker across {decisions.running_deployments} running book(s).",
    )


def _alert_payload(row: OperatorAlert) -> FleetAlertPayload:
    """Project one open fleet alert."""
    severity: Literal["info", "warning", "critical"] = (
        "critical"
        if row.severity is AlertSeverity.CRITICAL
        else "warning"
        if row.severity is AlertSeverity.WARNING
        else "info"
    )
    return FleetAlertPayload(
        subject=row.subject,
        severity=severity,
        detail=row.detail,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        occurrences=row.occurrences,
    )
