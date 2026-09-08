"""Operator diagnostics service behavior without PostgreSQL."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thytrader.config import Settings
from thytrader.execution.store import DisabledExecutionStore
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.operator.models import SCHEMA_VERSION, ReportStatus
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
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


def _diagnostics(*, drafts: DisabledStrategyDraftStore | None = None) -> OperatorDiagnostics:
    """Build diagnostics against demo portfolio and disabled durable stores."""
    return OperatorDiagnostics(
        settings=Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=DisabledStrategyPublicationStore(),
        drafts=drafts or DisabledStrategyDraftStore(),
        backtests=DisabledBacktestResultStore(),
        execution=DisabledExecutionStore(),
        audit=InMemoryAuditEventStore(),
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
