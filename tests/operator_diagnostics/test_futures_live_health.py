"""The operator health consumer exposes open futures alerts, not fake readiness."""

from dataclasses import replace

import pytest

from tests.execution_worker.test_fleet_entry_incident import _diagnostics
from tests.operator_diagnostics.test_futures_books_report import _AT
from thytrader.alerts.models import AlertCode, AlertScope, AlertSeverity, SupervisionFinding
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.operator.models import ReportStatus
from thytrader.trading.memory import InMemoryExecutionStore

pytestmark = pytest.mark.anyio


async def test_health_reads_futures_alerts() -> None:
    """A futures critical condition fails health through the actual diagnostics consumer."""
    alerts = InMemoryAlertStore()
    await alerts.record(
        SupervisionFinding(
            code=AlertCode.FUTURES_MARGIN_LOW,
            scope=AlertScope.FLEET,
            subject="futures:live",
            severity=AlertSeverity.CRITICAL,
            detail="Synthetic margin breach.",
        ),
        now=_AT,
    )
    diagnostics = await _diagnostics(InMemoryExecutionStore(), alerts)
    report = await diagnostics.health()
    component = next((row for row in report.components if row.name == "futures_live"), None)
    assert component is not None
    assert component.status is ReportStatus.FAILED
    assert component.reason_code == "FUTURES_MARGIN_LOW"


async def test_health_without_alert_storage_is_unknown_not_healthy() -> None:
    """No durable alert store cannot prove that supervision is clear."""
    diagnostics = await _diagnostics(InMemoryExecutionStore(), InMemoryAlertStore())
    diagnostics = replace(diagnostics, alert_store=None)
    report = await diagnostics.health()
    component = next((row for row in report.components if row.name == "futures_live"), None)
    assert component is not None
    assert component.status is ReportStatus.DEGRADED
