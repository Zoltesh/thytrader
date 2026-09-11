"""Operator diagnostics service behavior without PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.config import Settings
from thytrader.execution.store import DisabledExecutionStore
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.operator.models import SCHEMA_VERSION, ReportStatus
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.persistence.worker_heartbeats import (
    DisabledWorkerHeartbeatStore,
    InMemoryWorkerHeartbeatStore,
)
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.strategies.authoring import DisabledStrategyDraftStore, StrategyDraft
from thytrader.strategies.publication import DisabledStrategyPublicationStore

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition


class _RecordingDraftStore(DisabledStrategyDraftStore):
    """Count mutations so operator reads cannot hide a write."""

    def __init__(self) -> None:
        """Start with a zero create counter."""
        self.create_calls = 0

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return no drafts without recording a mutation."""
        return ()

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Count accidental creates."""
        self.create_calls += 1
        return await super().create_draft(definition)


def _diagnostics(
    *,
    drafts: DisabledStrategyDraftStore | None = None,
    settings: Settings | None = None,
    heartbeat_store: DisabledWorkerHeartbeatStore | InMemoryWorkerHeartbeatStore | None = None,
) -> OperatorDiagnostics:
    """Build diagnostics against demo portfolio and disabled durable stores."""
    return OperatorDiagnostics(
        settings=settings or Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=DisabledStrategyPublicationStore(),
        drafts=drafts or DisabledStrategyDraftStore(),
        backtests=DisabledBacktestResultStore(),
        execution=DisabledExecutionStore(),
        audit=InMemoryAuditEventStore(),
        heartbeat_store=heartbeat_store,
    )


def test_health_report_is_versioned_and_not_secretly_healthy() -> None:
    """A database-less process must emit v1 reports that are degraded, not healthy."""
    report = asyncio.run(_diagnostics().health())
    assert report.schema_version == SCHEMA_VERSION
    assert report.report_kind == "health"
    assert report.timezone == "UTC"
    assert report.redaction.secrets_redacted is True
    assert report.redaction.balances_omitted is True
    assert report.overall_status is not ReportStatus.HEALTHY
    names = {component.name for component in report.components}
    assert {"api", "database", "exchange", "portfolio_history"}.issubset(names)


def test_configuration_omits_raw_environment_and_credentials() -> None:
    """Configuration payload exposes flags, not secret values."""
    report = asyncio.run(_diagnostics().configuration())
    dumped = report.model_dump(mode="json")
    assert "THYTRADER_" not in str(dumped)
    assert dumped["payload"]["coinbase_credentials_configured"] is False
    assert dumped["payload"]["database_configured"] is False


def test_exchange_reports_demo_permissions_without_balances() -> None:
    """Demo connectivity is healthy enough to inspect, without account totals."""
    report = asyncio.run(_diagnostics().exchange())
    assert report.payload.demo is True
    assert report.payload.permissions
    dumped = report.model_dump(mode="json")
    assert "total_value" not in dumped
    assert "available" not in dumped


def test_health_does_not_create_strategy_drafts() -> None:
    """Read-only health must not call draft creation."""
    drafts = _RecordingDraftStore()
    asyncio.run(_diagnostics(drafts=drafts).health())
    assert drafts.create_calls == 0


def test_runtime_report_omits_cash_and_includes_deployments() -> None:
    """Runtime watch is a v1 report without balances."""
    report = asyncio.run(_diagnostics().runtime_report())
    assert report.schema_version == SCHEMA_VERSION
    assert report.report_kind == "runtime"
    dumped = report.model_dump(mode="json")
    assert "cash" not in dumped
    assert report.payload.deployments == ()
    assert report.redaction.balances_omitted is True


def test_health_reports_engine_missing_when_url_is_set_without_engine() -> None:
    """A configured database URL without an API engine is not treated as healthy."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://thytrader:unused@127.0.0.1:9/thytrader",
    )
    report = asyncio.run(_diagnostics(settings=settings).health())
    database = next(component for component in report.components if component.name == "database")
    assert database.reason_code == "DATABASE_ENGINE_MISSING"
    assert database.status is not ReportStatus.HEALTHY


def test_health_reports_unavailable_heartbeats_from_disabled_store() -> None:
    """Disabled heartbeat storage must not be treated as worker health."""
    report = asyncio.run(_diagnostics(heartbeat_store=DisabledWorkerHeartbeatStore()).health())
    workers = {
        component.name: component.reason_code
        for component in report.components
        if component.name.endswith("_worker")
    }
    assert workers["portfolio_worker"] == "HEARTBEAT_UNAVAILABLE"
    assert workers["market_data_worker"] == "HEARTBEAT_UNAVAILABLE"
    assert workers["execution_worker"] == "HEARTBEAT_UNAVAILABLE"


def test_health_reports_missing_and_fresh_heartbeats() -> None:
    """Empty heartbeat rows are missing; a fresh touch is ready."""

    async def _scenario() -> tuple[str, str]:
        empty = InMemoryWorkerHeartbeatStore()
        missing = await _diagnostics(heartbeat_store=empty).health()
        await empty.touch("portfolio_worker", datetime.now(UTC))
        await empty.touch("market_data_worker", datetime.now(UTC))
        await empty.touch("execution_worker", datetime.now(UTC))
        ready = await _diagnostics(heartbeat_store=empty).health()
        missing_code = next(
            component.reason_code
            for component in missing.components
            if component.name == "market_data_worker"
        )
        ready_code = next(
            component.reason_code
            for component in ready.components
            if component.name == "market_data_worker"
        )
        return missing_code, ready_code

    missing_code, ready_code = asyncio.run(_scenario())
    assert missing_code == "HEARTBEAT_MISSING"
    assert ready_code == "READY"
