"""Fleet entry block alerts: severity, recovery proof, and vanished scopes (ADR 0130)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
    new_alert,
)
from thytrader.alerts.supervision_fleet import fleet_entry_evidence, fleet_entry_finding
from thytrader.risk.fleet_entry_models import (
    BlockingBook,
    FleetAdmissibility,
    FleetBlockerClass,
    FleetEntryCheck,
    FleetEntryHealth,
    FleetScopeHealth,
)
from thytrader.trading.models import DeploymentMode

_NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def _scope(
    mode: DeploymentMode = DeploymentMode.LIVE,
    *checks: FleetEntryCheck,
    admissible: FleetAdmissibility = "yes",
) -> FleetScopeHealth:
    """One scope with the given checks."""
    return FleetScopeHealth(
        mode=mode,
        scope="USDC",
        entries_admissible=admissible,
        running_deployments=2,
        occupied_deployments=2,
        checks=checks,
    )


def _block(
    check_class: FleetBlockerClass, *, reason: str = "BREAKER_MARK_MISSING"
) -> FleetEntryCheck:
    """One fleet-wide block of the given class naming one book."""
    return FleetEntryCheck(
        name="daily_loss",
        status="blocked",
        detail="Daily-loss unavailable.",
        reason_code=reason,
        blocker_class=check_class,
        fleet_wide=True,
        books=(
            BlockingBook(
                deployment_id=uuid4(),
                status="stopped",
                product_id="BTC-USDC",
                detail="order X FILLED with filled_quantity 0 but fills sum 1",
            ),
        ),
    )


def test_live_evidence_block_is_critical() -> None:
    """A live scope blocked by missing evidence pages as critical."""
    finding = fleet_entry_finding(_scope(DeploymentMode.LIVE, _block("evidence")))
    assert finding is not None
    assert finding.severity is AlertSeverity.CRITICAL
    assert finding.scope is AlertScope.FLEET
    assert "order X FILLED with filled_quantity 0 but fills sum 1" in finding.detail


def test_latch_and_paper_blocks_are_warnings() -> None:
    """A latch, or any paper block, is a warning."""
    latch = fleet_entry_finding(_scope(DeploymentMode.LIVE, _block("latch")))
    paper = fleet_entry_finding(_scope(DeploymentMode.PAPER, _block("evidence")))
    assert latch is not None and latch.severity is AlertSeverity.WARNING
    assert paper is not None and paper.severity is AlertSeverity.WARNING


def test_transient_block_raises_nothing_and_proves_recovery() -> None:
    """The clustering window is not alerted, and a scope blocked only by it is recovered."""
    scope = _scope(DeploymentMode.LIVE, _block("transient", reason="FLEET_ENTRY_CLUSTER_LIMIT"))
    evidence = fleet_entry_evidence(FleetEntryHealth(_NOW, True, (scope,)))
    assert evidence.findings == ()
    assert evidence.evaluated == (AlertCheck(AlertCode.FLEET_ENTRIES_BLOCKED, "fleet:live:USDC"),)


def test_unknown_check_never_resolves() -> None:
    """Unknown evidence proves no recovery."""
    unknown = FleetEntryCheck(name="btc_beta", status="unknown")
    evidence = fleet_entry_evidence(
        FleetEntryHealth(_NOW, True, (_scope(DeploymentMode.LIVE, unknown),))
    )
    assert evidence.findings == ()
    assert evidence.evaluated == ()


def test_incomplete_evaluation_proves_nothing() -> None:
    """An unlistable fleet neither raises nor resolves."""
    assert fleet_entry_evidence(FleetEntryHealth(_NOW, False, ())).evaluated == ()


def test_vanished_scope_resolves_its_alert() -> None:
    """A scope with no occupied book left cannot block entries; its alert resolves."""
    finding = SupervisionFinding(
        code=AlertCode.FLEET_ENTRIES_BLOCKED,
        scope=AlertScope.FLEET,
        subject="fleet:paper:USDT",
        severity=AlertSeverity.WARNING,
        detail="old",
    )
    prior = replace(new_alert(finding, now=_NOW), occurrences=3)
    evidence = fleet_entry_evidence(FleetEntryHealth(_NOW, True, ()), prior_alerts=(prior,))
    assert evidence.evaluated == (AlertCheck(AlertCode.FLEET_ENTRIES_BLOCKED, "fleet:paper:USDT"),)
