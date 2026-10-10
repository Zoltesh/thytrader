"""Regression: the 2026-10-10 fleet entry block is visible everywhere (ADR 0130).

One legacy order of a stopped live book (FILLED with ``filled_quantity`` 0 but one applied
fill of 0.00014174) made the daily-loss breaker fail closed for every live USDC entry, while
``health``, ``risk`` and ``readiness`` looked healthy. Rebuilt record for record, it must now
show up in the worker's durable alert, the ``fleet-health`` report, the ``risk`` and
``readiness`` sections and the ``health`` status, and clear everywhere after the repair.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.fleet_incident_support import (
    EXPECTED_GAP,
    INCIDENT_AT,
    JTO_DEPLOYMENT_ID,
    LEGACY_DEPLOYMENT_ID,
    NEAR_DEPLOYMENT_ID,
    incident_policy,
    repair_legacy_order,
    seeded_store,
)
from thytrader.alerts.models import AlertCode, AlertScope, AlertSeverity
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.backtest.results import DisabledBacktestResultStore
from thytrader.config import Settings
from thytrader.execution import fleet_entry_evidence
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service as worker_service
from thytrader.execution_worker.fleet_supervision import _supervise_fleet_entries
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.operator.models import ReportStatus
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.trading.memory import InMemoryExecutionStore

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _incident_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluate at the incident's wall-clock time."""
    monkeypatch.setattr(fleet_entry_evidence, "utc_now", lambda: INCIDENT_AT)


def _alerts() -> tuple[InMemoryAlertStore, AlertService]:
    """An in-memory alert feed with the default thresholds and no delivery."""
    store = InMemoryAlertStore()
    return store, AlertService(store, DisabledNotificationSender(), thresholds=AlertThresholds())


async def _diagnostics(
    store: InMemoryExecutionStore,
    alert_store: InMemoryAlertStore,
    journal: InMemoryDecisionJournalStore | None = None,
) -> OperatorDiagnostics:
    """Operator diagnostics over the incident stores with the live policy published."""
    policies = InMemoryRiskPolicyStore()
    await policies.publish(incident_policy())
    return OperatorDiagnostics(
        settings=Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=DisabledStrategySnapshotStore(),
        strategies_store=DisabledStrategyStore(),
        backtests=DisabledBacktestResultStore(),
        execution=store,
        audit=InMemoryAuditEventStore(),
        market_data=MarketDataService(DemoMarketData()),
        risk_policies=policies,
        decision_store=journal,
        alert_store=alert_store,
    )


async def _supervise(
    store: InMemoryExecutionStore, alerts: AlertService, *, at: datetime = INCIDENT_AT
) -> None:
    """Run one worker fleet supervision pass at ``at``."""
    await _supervise_fleet_entries(
        alert_service=alerts,
        store=store,
        market_data=MarketDataService(DemoMarketData()),
        policy=incident_policy(),
        observed_at=at,
    )


def _blocked_bar(deployment_id: UUID, product_id: str, *, hours_ago: int) -> BarDecision:
    """One matched signal the gate denied for missing daily-loss evidence."""
    closes = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=hours_ago
    )
    return BarDecision(
        deployment_id=deployment_id,
        product_id=product_id,
        timeframe="2h",
        mode=DeploymentMode.LIVE,
        bar_starts_at=closes - timedelta(hours=2),
        bar_closes_at=closes,
        evaluated_at=closes,
        outcome=DecisionOutcome.ENTRY_BLOCKED,
        reason_code="BREAKER_MARK_MISSING",
        summary="Entry blocked: daily-loss evidence unavailable.",
    )


async def test_incident_raises_one_critical_alert_and_resolves_after_repair() -> None:
    """Raised on the first evaluation, deduplicated while it lasts, resolved on repair."""
    store = await seeded_store()
    feed, alerts = _alerts()
    await _supervise(store, alerts)
    (alert,) = await feed.list_open_alerts()
    assert alert.code is AlertCode.FLEET_ENTRIES_BLOCKED
    assert alert.scope is AlertScope.FLEET
    assert alert.subject == "fleet:live:USDC"
    assert alert.severity is AlertSeverity.CRITICAL
    assert alert.deployment_id == LEGACY_DEPLOYMENT_ID
    assert EXPECTED_GAP in alert.detail
    assert "BREAKER_MARK_MISSING" in alert.detail

    await _supervise(store, alerts, at=INCIDENT_AT + timedelta(seconds=30))
    (again,) = await feed.list_open_alerts()
    assert again.id == alert.id
    assert again.occurrences == 2

    await repair_legacy_order(store)
    await _supervise(store, alerts, at=INCIDENT_AT + timedelta(seconds=60))
    assert await feed.list_open_alerts() == ()
    (resolved,) = feed.history[(AlertCode.FLEET_ENTRIES_BLOCKED.value, "fleet:live:USDC")]
    assert resolved.resolved_at == INCIDENT_AT + timedelta(seconds=60)


async def test_incident_fails_health_and_shows_in_every_report() -> None:
    """Health fails, fleet-health/risk/readiness name the order; all clear after repair."""
    store = await seeded_store()
    feed, alerts = _alerts()
    journal = InMemoryDecisionJournalStore()
    await journal.upsert(_blocked_bar(NEAR_DEPLOYMENT_ID, "NEAR-USDC", hours_ago=2))
    await journal.upsert(_blocked_bar(JTO_DEPLOYMENT_ID, "JTO-USDC", hours_ago=4))
    await _supervise(store, alerts)
    diagnostics = await _diagnostics(store, feed, journal)

    health = await diagnostics.health()
    fleet = next(item for item in health.components if item.name == "fleet_entries")
    assert fleet.status is ReportStatus.FAILED
    assert fleet.reason_code == "FLEET_ENTRIES_BLOCKED"
    assert health.overall_status is ReportStatus.FAILED

    report = await diagnostics.fleet_health()
    assert report.report_kind == "fleet_health"
    assert report.overall_status is ReportStatus.FAILED
    entries = report.payload.entries
    assert entries.live_entries_admissible == "blocked"
    (scope,) = entries.scopes
    assert (scope.mode, scope.scope, scope.entries_admissible) == ("live", "USDC", "blocked")
    assert scope.reason_codes == ("BREAKER_MARK_MISSING",)
    assert scope.blocking_deployment_ids == (LEGACY_DEPLOYMENT_ID,)
    daily = next(check for check in scope.checks if check.check == "daily_loss")
    assert EXPECTED_GAP in daily.deployments[0].detail
    assert [item.subject for item in report.payload.open_fleet_alerts] == ["fleet:live:USDC"]
    systemic = report.payload.decisions.systemic
    assert [(item.kind, item.reason_code, item.deployments) for item in systemic] == [
        ("evidence_block", "BREAKER_MARK_MISSING", 2)
    ]

    risk = await diagnostics.risk()
    assert risk.payload.fleet_entries is not None
    assert risk.payload.fleet_entries.live_entries_admissible == "blocked"
    assert risk.overall_status is ReportStatus.FAILED
    readiness = await diagnostics.readiness_report()
    assert readiness.payload.fleet_entries is not None
    assert readiness.payload.fleet_entries.scopes[0].blocking_deployment_ids == (
        LEGACY_DEPLOYMENT_ID,
    )
    assert any(item.reason_code == "FLEET_ENTRIES_BLOCKED" for item in readiness.components)

    await repair_legacy_order(store)
    await _supervise(store, alerts, at=INCIDENT_AT + timedelta(seconds=30))
    healed = await diagnostics.health()
    fleet = next(item for item in healed.components if item.name == "fleet_entries")
    assert fleet.status is ReportStatus.HEALTHY
    after = await diagnostics.fleet_health()
    assert after.payload.entries.live_entries_admissible == "yes"
    assert after.payload.open_fleet_alerts == ()


async def test_worker_cycle_runs_fleet_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    """The execution worker evaluates the fleet every cycle, after per-book supervision."""

    async def _noop(**kwargs: object) -> None:
        """Skip per-book processing; only supervision matters here."""
        del kwargs

    monkeypatch.setattr(worker_service, "_process_one", _noop)
    store = await seeded_store()
    feed, alerts = _alerts()
    await worker_service._run_cycle(
        store=store,
        publication_store=InMemoryStrategyStore(),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_store=None,
        alert_service=alerts,
        worker_interval_seconds=30,
    )
    subjects = {row.subject for row in await feed.list_open_alerts()}
    assert "fleet:live:USDC" in subjects


async def test_alerts_report_lists_the_fleet_scope() -> None:
    """The ADR 0115 feed renders the fleet scope instead of failing validation."""
    store = await seeded_store()
    feed, alerts = _alerts()
    await _supervise(store, alerts)
    diagnostics = await _diagnostics(store, feed)
    report = await diagnostics.alerts()
    (item,) = report.payload.open_alerts
    assert (item.scope, item.code, item.severity) == ("fleet", "FLEET_ENTRIES_BLOCKED", "critical")
