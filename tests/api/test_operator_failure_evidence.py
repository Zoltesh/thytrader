"""Versioned HTTP responses preserve diagnostic evidence without raw provider detail."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from requests import Timeout

from tests.exchanges.test_coinbase_read_errors import _FailedAccountClient
from tests.operator_diagnostics.test_service import _diagnostics
from thytrader.api.app import create_app
from thytrader.api.routes.operator import get_operator_diagnostics
from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    InMemoryAuditEventStore,
)
from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.operator.models import ExchangeReport, ReconciliationReport
from thytrader.portfolio.service import PortfolioService
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from thytrader.operator.service import OperatorDiagnostics


def test_exchange_http_response_keeps_safe_failure_evidence() -> None:
    """The HTTP shape matches the typed CLI response and omits raw timeout text."""
    portfolio = PortfolioService(CoinbaseAccount(_FailedAccountClient(Timeout("secret-token"))))
    diagnostics = replace(_diagnostics(), portfolio=portfolio)

    def diagnostic_override() -> OperatorDiagnostics:
        """Supply deterministic transport failure through the real GET handler."""
        return diagnostics

    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_operator_diagnostics] = diagnostic_override
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/exchange")
    assert response.status_code == 200
    report = ExchangeReport.model_validate(response.json())
    assert report.payload.failure is not None
    assert report.payload.failure.operation == "balances"
    assert report.payload.failure.kind == "timeout"
    assert "secret-token" not in response.text


def test_reconciliation_http_response_links_recovered_failure() -> None:
    """Observed recovery survives HTTP serialization and report validation."""
    audit = InMemoryAuditEventStore()
    now = datetime(2026, 10, 4, tzinfo=UTC)
    failure = AuditEvent(
        occurred_at=now,
        category=AuditEventCategory.WEBSOCKET,
        action="user_websocket_connection_failed",
        outcome=AuditEventOutcome.FAILURE,
        provider="coinbase",
        detail="secret-token",
    )
    recovery = AuditEvent(
        occurred_at=now + timedelta(seconds=2),
        category=AuditEventCategory.WEBSOCKET,
        action="user_websocket_state_connected",
        outcome=AuditEventOutcome.INFO,
        provider="coinbase",
    )
    asyncio.run(audit.append(failure))
    asyncio.run(audit.append(recovery))
    diagnostics = replace(_diagnostics(), audit=audit, execution=InMemoryExecutionStore())

    def diagnostic_override() -> OperatorDiagnostics:
        """Supply the bounded audit evidence through the real reconciliation handler."""
        return diagnostics

    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_operator_diagnostics] = diagnostic_override
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/reconciliation")
    assert response.status_code == 200
    report = ReconciliationReport.model_validate(response.json())
    evidence = report.payload.findings[0].audit_event
    assert evidence is not None
    assert evidence.event_id == failure.id
    assert evidence.recovery_event_id == recovery.id
    assert evidence.recovery_status == "recovered"
    assert "secret-token" not in response.text
