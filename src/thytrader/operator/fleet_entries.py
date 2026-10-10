"""Fleet entry readiness section shared by ``fleet-health``, ``readiness`` and ``risk``.

The section is evaluated fresh from the API process's stores the way the execution worker
evaluates it for the alert (``execution.fleet_entry_evidence``), and graded into one
component:

- ``FAILED`` ``FLEET_ENTRIES_BLOCKED``: a live scope is blocked by missing evidence;
- ``DEGRADED`` ``FLEET_ENTRIES_BLOCKED``: any other alertable block (latch, policy, paper);
- ``DEGRADED`` ``FLEET_ENTRY_CAPACITY_FULL``: only unalerted blocks remain: a fully
  invested fleet (slots or exposure caps), the clustering window or a fleet disarm;
- ``DEGRADED`` ``FLEET_ENTRIES_UNKNOWN``: a check or the fleet listing could not be read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader.execution.fleet_entry_evidence import load_fleet_entry_health
from thytrader.operator.fleet_health_models import (
    FleetBlockingBookPayload,
    FleetEntriesPayload,
    FleetEntryCheckPayload,
    FleetEntryScopePayload,
)
from thytrader.operator.models import ComponentReport, ReportStatus
from thytrader.risk.fleet_entry_models import (
    UNALERTED_CLASSES,
    FleetEntryHealth,
    scope_subject,
)
from thytrader.risk.store import load_effective_policy
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.market_data.service import MarketDataService
    from thytrader.operator.fleet_health_models import Admissibility
    from thytrader.risk.fleet_entry_models import FleetEntryCheck, FleetScopeHealth
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.store import ExecutionStore

FLEET_COMPONENT = "fleet_entries"


async def fleet_entries_payload(
    execution: ExecutionStore,
    risk_policies: RiskPolicyStore | None,
    *,
    market_data: MarketDataService | None,
    futures_account: FuturesAccountSnapshotStore | None,
) -> FleetEntriesPayload:
    """Evaluate fleet entry readiness now; an unreadable policy is unknown, not the default."""
    try:
        policy = (await load_effective_policy(risk_policies)).definition
    except Exception:  # noqa: BLE001 - policy store failures make the section unknown.
        return entries_payload(
            FleetEntryHealth(
                evaluated_at=datetime.now(UTC),
                complete=False,
                scopes=(),
                detail="The effective risk policy could not be loaded.",
            )
        )
    health = await load_fleet_entry_health(
        execution, policy, market_data=market_data, futures_account=futures_account
    )
    return entries_payload(health)


def entries_payload(health: FleetEntryHealth) -> FleetEntriesPayload:
    """Project one evaluation into the operator payload."""
    scopes = tuple(_scope_payload(scope) for scope in health.scopes)
    return FleetEntriesPayload(
        evaluated_at=health.evaluated_at,
        complete=health.complete,
        detail=health.detail,
        live_entries_admissible=_mode_admissibility(health, DeploymentMode.LIVE),
        paper_entries_admissible=_mode_admissibility(health, DeploymentMode.PAPER),
        scopes=scopes,
    )


def fleet_entries_component(payload: FleetEntriesPayload) -> ComponentReport:
    """Grade the section: live evidence blocks fail; other blocks and unknowns degrade."""
    if not payload.complete:
        return ComponentReport(
            name=FLEET_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="FLEET_ENTRIES_UNKNOWN",
            detail=payload.detail or "Fleet entry readiness could not be evaluated.",
        )
    blocked = [scope for scope in payload.scopes if scope.entries_admissible == "blocked"]
    alertable = [scope for scope in blocked if _alertable(scope)]
    if alertable:
        failed = any(scope.mode == "live" and _evidence_block(scope) for scope in alertable)
        return ComponentReport(
            name=FLEET_COMPONENT,
            status=ReportStatus.FAILED if failed else ReportStatus.DEGRADED,
            reason_code="FLEET_ENTRIES_BLOCKED",
            detail=_blocked_detail(alertable),
        )
    if blocked:
        return ComponentReport(
            name=FLEET_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="FLEET_ENTRY_CAPACITY_FULL",
            detail=_blocked_detail(blocked),
        )
    if any(scope.entries_admissible == "unknown" for scope in payload.scopes):
        return ComponentReport(
            name=FLEET_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="FLEET_ENTRIES_UNKNOWN",
            detail="At least one fleet entry check could not be evaluated; see checks.",
        )
    return ComponentReport(
        name=FLEET_COMPONENT,
        status=ReportStatus.HEALTHY,
        reason_code="OK",
        detail=f"New entries are admissible in all {len(payload.scopes)} occupied scope(s).",
    )


def _alertable(scope: FleetEntryScopePayload) -> bool:
    """A fleet-wide block that needs a repair, reset or decision (the alerted classes)."""
    return any(
        check.status == "blocked"
        and check.fleet_wide
        and check.blocker_class not in UNALERTED_CLASSES
        for check in scope.checks
    )


def _evidence_block(scope: FleetEntryScopePayload) -> bool:
    """A fleet-wide block caused by missing or unreadable evidence."""
    return any(
        check.status == "blocked" and check.fleet_wide and check.blocker_class == "evidence"
        for check in scope.checks
    )


def _blocked_detail(scopes: Sequence[FleetEntryScopePayload]) -> str:
    """Name each blocked scope and its codes within the component detail bound."""
    parts = [f"{scope.mode} {scope.scope}: {','.join(scope.reason_codes)}" for scope in scopes]
    return f"New entries are blocked fleet-wide in {'; '.join(parts)}."[:500]


def _mode_admissibility(health: FleetEntryHealth, mode: DeploymentMode) -> Admissibility:
    """The worst scope of one mode; unknown when the fleet could not be read."""
    if not health.complete:
        return "unknown"
    states = {scope.entries_admissible for scope in health.scopes if scope.mode is mode}
    if "blocked" in states:
        return "blocked"
    if "unknown" in states:
        return "unknown"
    return "yes"


def _scope_payload(scope: FleetScopeHealth) -> FleetEntryScopePayload:
    """One scope with every check and its blocking books."""
    return FleetEntryScopePayload(
        mode="live" if scope.mode is DeploymentMode.LIVE else "paper",
        scope=scope.scope,
        entries_admissible=scope.entries_admissible,
        reason_codes=scope.reason_codes,
        blocking_deployment_ids=scope.blocking_deployment_ids,
        running_deployments=scope.running_deployments,
        occupied_deployments=scope.occupied_deployments,
        alert_subject=scope_subject(scope.mode, scope.scope),
        checks=tuple(_check_payload(check) for check in scope.checks),
    )


def _check_payload(check: FleetEntryCheck) -> FleetEntryCheckPayload:
    """One check with its books, details bounded for the report."""
    return FleetEntryCheckPayload(
        check=check.name,
        status=check.status,
        reason_code=check.reason_code,
        blocker_class=check.blocker_class,
        fleet_wide=check.fleet_wide,
        detail=check.detail[:1000],
        deployments=tuple(
            FleetBlockingBookPayload(
                deployment_id=book.deployment_id,
                status=_status(book.status),
                product_id=book.product_id,
                detail=book.detail[:1000],
            )
            for book in check.books
        ),
    )


def _status(value: str) -> Literal["running", "paused", "stopped"]:
    """Narrow a deployment status string to the payload literal."""
    if value == "running":
        return "running"
    if value == "paused":
        return "paused"
    return "stopped"
