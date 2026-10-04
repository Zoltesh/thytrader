"""Historical audit failures expose exact identities and conservative recovery evidence."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tests.operator_diagnostics.test_service import _diagnostics
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.operator.audit_findings import audit_failure_findings
from thytrader.operator.models import ReportStatus
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    InMemoryAuditEventStore,
)

_NOW = datetime(2026, 10, 4, tzinfo=UTC)


def _event(action: str, outcome: AuditEventOutcome, seconds: int = 0) -> AuditEvent:
    """Build one provider-scoped audit observation, with intentionally sensitive detail."""
    return AuditEvent(
        occurred_at=_NOW + timedelta(seconds=seconds),
        category=AuditEventCategory.WEBSOCKET,
        action=action,
        outcome=outcome,
        provider="coinbase",
        detail="secret-key account-identity balance=12345",
    )


def test_recovered_failure_remains_a_finding_without_raw_detail() -> None:
    """Recovery is an evidence link, never deletion or proof of current feed health."""
    failure = _event("user_websocket_connection_failed", AuditEventOutcome.FAILURE)
    recovered = _event("user_websocket_state_connected", AuditEventOutcome.INFO, 2)
    findings = audit_failure_findings((recovered, failure))
    assert len(findings) == 1
    evidence = findings[0].audit_event
    assert evidence is not None
    assert evidence.event_id == failure.id
    assert evidence.occurred_at == failure.occurred_at
    assert evidence.recovery_status == "recovered"
    assert evidence.recovery_event_id == recovered.id
    assert evidence.recovered_at == recovered.occurred_at
    assert "secret-key" not in findings[0].model_dump_json()
    assert "balance=" not in findings[0].model_dump_json()


@pytest.mark.parametrize("changed", ["provider", "product_id", "category", "time", "outcome"])
def test_unrelated_success_does_not_clear_failure(changed: str) -> None:
    """Only a later non-failure observation on the same feed can establish recovery."""
    failure = _event("user_websocket_connection_failed", AuditEventOutcome.FAILURE)
    recovered = _event("user_websocket_state_connected", AuditEventOutcome.INFO, 2)
    changes: dict[str, object] = {
        "provider": "other",
        "product_id": "ETH-USDC",
        "category": AuditEventCategory.CONNECTION,
        "time": _NOW - timedelta(seconds=1),
        "outcome": AuditEventOutcome.FAILURE,
    }
    field = "occurred_at" if changed == "time" else changed
    recovered = recovered.model_copy(update={field: changes[changed]})
    evidence = audit_failure_findings((recovered, failure))[-1].audit_event
    assert evidence is not None
    assert evidence.recovery_status == "unresolved"
    assert evidence.recovery_event_id is None


def test_order_failure_has_unknown_recovery_despite_later_success() -> None:
    """A generic success cannot waive an ambiguous order submission failure."""
    failure = _event("order_submit_failed", AuditEventOutcome.FAILURE)
    recovered = _event("user_websocket_state_connected", AuditEventOutcome.INFO, 2)
    evidence = audit_failure_findings((recovered, failure))[0].audit_event
    assert evidence is not None
    assert evidence.recovery_status == "unknown"


def test_reconciliation_keeps_recovered_audit_failure_degraded() -> None:
    """The report preserves historical evidence and its existing fail-closed status."""

    async def exercise() -> None:
        """Record matching events and inspect the public diagnostic report."""
        audit = InMemoryAuditEventStore()
        await audit.append(_event("websocket_connection_failed", AuditEventOutcome.FAILURE))
        await audit.append(_event("websocket_state_connected", AuditEventOutcome.INFO, 2))
        diagnostics = replace(_diagnostics(), audit=audit, execution=InMemoryExecutionStore())
        report = await diagnostics.reconciliation()
        assert report.overall_status is ReportStatus.DEGRADED
        evidence = report.payload.findings[0].audit_event
        assert evidence is not None
        assert evidence.recovery_status == "recovered"

    asyncio.run(exercise())
