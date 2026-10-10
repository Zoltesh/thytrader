"""Operator fleet-health: decision-log aggregation, health component, HTTP and CLI (ADR 0130)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tests.fleet_incident_support import seeded_store
from thytrader.alerts.models import AlertCode, AlertScope, AlertSeverity, SupervisionFinding
from thytrader.alerts.store import DisabledAlertStore, InMemoryAlertStore
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome, DecisionSkipReason
from thytrader.operator.cli import _parser
from thytrader.operator.fleet_decisions import aggregate_decisions, fleet_decision_log
from thytrader.operator.fleet_health_models import FleetHealthReport
from thytrader.operator.fleet_health_report import fleet_alert_health_component
from thytrader.operator.http import _REPORT_MODELS
from thytrader.operator.models import REPORT_KINDS, ReportStatus
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)

_NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def _row(
    deployment_id: UUID,
    *,
    outcome: DecisionOutcome = DecisionOutcome.ENTRY_BLOCKED,
    reason: str = "BREAKER_MARK_MISSING",
    skip: DecisionSkipReason | None = None,
    hours_ago: int = 1,
) -> BarDecision:
    """One blocked or skipped bar closing ``hours_ago`` before ``_NOW``."""
    closes = _NOW - timedelta(hours=hours_ago)
    return BarDecision(
        deployment_id=deployment_id,
        product_id="SOL-USDC",
        timeframe="1h",
        mode=DeploymentMode.LIVE,
        bar_starts_at=closes - timedelta(hours=1),
        bar_closes_at=closes,
        evaluated_at=closes,
        outcome=outcome,
        reason_code=reason,
        skip_reason=skip,
        summary="test",
    )


def _running(
    deployment_id: UUID, status: DeploymentStatus = DeploymentStatus.RUNNING
) -> Deployment:
    """A live book row."""
    return Deployment(
        id=deployment_id,
        strategy_fingerprint=None,
        strategy_id=uuid4(),
        kind=DeploymentKind.STRATEGY,
        product_id="SOL-USDC",
        mode=DeploymentMode.LIVE,
        status=status,
        cash=Decimal("0"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW - timedelta(days=3),
        updated_at=_NOW,
    )


def test_aggregation_flags_evidence_sizing_and_shared_reasons() -> None:
    """Evidence blocks, repeated sizing skips and shared reasons are systemic; routine is not."""
    one, two, three = uuid4(), uuid4(), uuid4()
    rows = [
        _row(one, reason="BTC_BETA_UNAVAILABLE"),
        _row(
            one,
            outcome=DecisionOutcome.SKIPPED,
            reason="NOTIONAL_BELOW_MINIMUM",
            skip=DecisionSkipReason.ENTRY_SIZING,
            hours_ago=2,
        ),
        _row(
            one,
            outcome=DecisionOutcome.SKIPPED,
            reason="NOTIONAL_BELOW_MINIMUM",
            skip=DecisionSkipReason.ENTRY_SIZING,
            hours_ago=3,
        ),
        _row(
            two,
            outcome=DecisionOutcome.SKIPPED,
            reason="DATA_GAP",
            skip=DecisionSkipReason.DATA_GAP,
        ),
        _row(
            three,
            outcome=DecisionOutcome.SKIPPED,
            reason="DATA_GAP",
            skip=DecisionSkipReason.DATA_GAP,
        ),
        *(
            _row(
                item,
                outcome=DecisionOutcome.SKIPPED,
                reason="WARMUP",
                skip=DecisionSkipReason.WARMUP,
            )
            for item in (one, two, three)
        ),
        _row(two, reason="DAILY_LOSS_LIMIT"),
    ]
    reasons, systemic = aggregate_decisions(rows)
    assert reasons[0].reason_code == "WARMUP"
    assert reasons[0].deployments == 3
    flagged = {(item.kind, item.reason_code) for item in systemic}
    assert flagged == {
        ("evidence_block", "BTC_BETA_UNAVAILABLE"),
        ("sizing_skips", "NOTIONAL_BELOW_MINIMUM"),
        ("shared_reason", "DATA_GAP"),
    }


def test_aggregation_flags_a_bot_stuck_in_warmup() -> None:
    """Twelve warmup skips in the window mean the indicators may never compute."""
    stuck = uuid4()
    rows = [
        _row(
            stuck,
            outcome=DecisionOutcome.SKIPPED,
            reason="WARMUP",
            skip=DecisionSkipReason.WARMUP,
            hours_ago=hour,
        )
        for hour in range(1, 13)
    ]
    _reasons, systemic = aggregate_decisions(rows)
    assert [(item.kind, item.deployment_ids) for item in systemic] == [("warmup_stuck", (stuck,))]


def test_decision_log_reads_running_books_inside_the_window() -> None:
    """Only running books are read, and bars older than 24 h are left out."""
    running, paused = uuid4(), uuid4()
    journal = InMemoryDecisionJournalStore()

    async def scenario() -> tuple[int, int]:
        await journal.upsert(_row(running, hours_ago=1))
        await journal.upsert(_row(running, hours_ago=30))
        await journal.upsert(_row(paused, hours_ago=1))
        payload = await fleet_decision_log(
            journal,
            (_running(running), _running(paused, DeploymentStatus.PAUSED)),
            now=_NOW,
        )
        return payload.rows_read, payload.running_deployments

    assert asyncio.run(scenario()) == (1, 1)


def test_decision_log_without_a_journal_is_unavailable() -> None:
    """No durable journal is unavailable, never an empty clean window."""
    payload = asyncio.run(fleet_decision_log(None, (), now=_NOW))
    assert payload.storage == "unavailable"


def test_health_component_reads_open_fleet_alerts() -> None:
    """Unreadable feed degrades; a warning degrades; nothing open is healthy."""

    async def scenario() -> tuple[str, str, str]:
        empty = await fleet_alert_health_component(InMemoryAlertStore())
        missing = await fleet_alert_health_component(DisabledAlertStore())
        store = InMemoryAlertStore()
        await store.record(
            SupervisionFinding(
                code=AlertCode.FLEET_ENTRIES_BLOCKED,
                scope=AlertScope.FLEET,
                subject="fleet:paper:USDC",
                severity=AlertSeverity.WARNING,
                detail="Paper USDC entries are blocked fleet-wide (daily_loss=DAILY_LOSS_LIMIT).",
            ),
            now=_NOW,
        )
        warned = await fleet_alert_health_component(store)
        return empty.status.value, missing.reason_code, warned.status.value

    assert asyncio.run(scenario()) == (
        ReportStatus.HEALTHY.value,
        "FLEET_ENTRY_ALERTS_UNAVAILABLE",
        ReportStatus.DEGRADED.value,
    )


def test_fleet_health_is_a_registered_report() -> None:
    """The kind, HTTP model and CLI subcommand are registered together."""
    assert "fleet_health" in REPORT_KINDS
    assert _REPORT_MODELS["fleet-health"] is FleetHealthReport
    assert _parser().parse_args(["fleet-health"]).command == "fleet-health"


def test_fleet_health_route_reports_the_incident() -> None:
    """GET /api/v1/operator/fleet-health is read-only and names the blocking book."""
    store = asyncio.run(seeded_store())
    app = create_app(settings=Settings(_env_file=None), execution_store=store)
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/fleet-health")
    assert response.status_code == 200
    body = response.json()
    assert body["report_kind"] == "fleet_health"
    report = FleetHealthReport.model_validate(body)
    assert report.payload.entries.live_entries_admissible == "blocked"
