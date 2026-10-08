"""Correlate bounded audit failures with explicit recovery observations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from thytrader.audit_events import AuditEvent, AuditEventOutcome
from thytrader.operator.runtime_models import AuditFailureEvidence, ReconciliationFinding

if TYPE_CHECKING:
    from collections.abc import Sequence

_RECOVERY_ACTIONS = {
    "user_websocket_connection_failed": "user_websocket_state_connected",
    "user_websocket_heartbeat_timeout": "user_websocket_state_connected",
    "websocket_connection_failed": "websocket_state_connected",
    "websocket_heartbeat_timeout": "websocket_state_connected",
}


def audit_failure_findings(events: Sequence[AuditEvent]) -> tuple[ReconciliationFinding, ...]:
    """Link each failure to evidence without copying raw details or clearing findings.

    Only known WebSocket recovery pairs match, on category, provider and product,
    strictly after the failure. Missing or unrelated successes never prove recovery.
    Both recovered and unresolved failures remain visible in the bounded audit window.
    """
    findings: list[ReconciliationFinding] = []
    for event in events:
        if event.outcome is not AuditEventOutcome.FAILURE:
            continue
        recovery_action = _RECOVERY_ACTIONS.get(event.action)
        recovery = _matching_recovery(event, recovery_action, events)
        status: Literal["recovered", "unresolved", "unknown"] = (
            "unknown" if recovery_action is None else "unresolved"
        )
        if recovery is not None:
            status = "recovered"
        findings.append(
            ReconciliationFinding(
                reason_code="AUDIT_FAILURES",
                deployment_id=None,
                detail=(
                    f"{event.action} failed at {event.occurred_at.isoformat()}; recovery {status}."
                ),
                audit_event=AuditFailureEvidence(
                    event_id=event.id,
                    occurred_at=event.occurred_at,
                    action=event.action,
                    provider=event.provider,
                    product_id=event.product_id,
                    recovery_status=status,
                    recovery_event_id=None if recovery is None else recovery.id,
                    recovered_at=None if recovery is None else recovery.occurred_at,
                ),
            )
        )
    return tuple(findings)


def _matching_recovery(
    failure: AuditEvent, action: str | None, events: Sequence[AuditEvent]
) -> AuditEvent | None:
    """Return the earliest matching later recovery from the inspected events only."""
    if action is None:
        return None
    matching = [
        event
        for event in events
        if event.action == action
        and event.outcome is not AuditEventOutcome.FAILURE
        and event.category == failure.category
        and event.provider == failure.provider
        and event.product_id == failure.product_id
        and event.occurred_at > failure.occurred_at
    ]
    return min(matching, key=lambda event: event.occurred_at) if matching else None
