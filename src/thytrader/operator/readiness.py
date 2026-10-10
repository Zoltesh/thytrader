"""Operator ``readiness`` report: advisory preflight before risk is armed (ADR 0114).

Read-only. The report answers "if these books run as allocated, what do the venue
balance, the account, and each portfolio actually permit?" It shows allocation
commitments versus the venue quote balance versus the account and portfolio exposure
caps, per-asset caps, remaining entry capacity, paper fee assumptions versus the
account's fee evidence, and both breaker tiers side by side.

It never tightens, publishes, or changes risk policy, allocations, or bot state.
Overcommitment of allocations is reported as an *advisory*: allocations are sizing
limits, not reserved funds, and the entry gate already denies orders at the cap. An
*actual* exposure violation (position cost plus working entries above the effective cap) is
reported as a violation finding. Amounts are exact ``Decimal`` values rendered as
canonical decimal strings; quote currencies are never summed across each other, and
books whose quote differs from the policy quote are disclosed instead of folded into
account totals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.operator.fleet_entries import fleet_entries_component
from thytrader.operator.models import PORTFOLIO_REDACTION, ComponentReport, ReportStatus
from thytrader.operator.readiness_account import (
    _account_section,
    _inventory_evidence,
    _inventory_unresolved,
    _read_venue,
    _VenueRead,
)
from thytrader.operator.readiness_fees import _fee_evidence, _paper_section
from thytrader.operator.readiness_futures import futures_section
from thytrader.operator.readiness_models import (
    ReadinessFeeEvidence,
    ReadinessFinding,
    ReadinessPayload,
    ReadinessReport,
    ReadinessSeverity,
)
from thytrader.operator.readiness_portfolio import (
    ReadinessPortfolioDirectory,
    _aggregate_for,
    _deployment_row,
    _load_portfolios,
    _portfolio_section,
    _scope_findings,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.portfolios.deployment import members
from thytrader.risk.store import load_effective_policy
from thytrader.trading.exposure import risk_bearing_snapshots
from thytrader.trading.models import Deployment, DeploymentMode, DeploymentSnapshot
from thytrader.trading.store import DisabledExecutionStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.operator.fleet_health_models import FleetEntriesPayload
    from thytrader.portfolio.service import PortfolioService
    from thytrader.risk.models import ActiveRiskPolicy
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.store import ExecutionStore

__all__ = [
    "ReadinessFeeEvidence",
    "ReadinessFinding",
    "ReadinessPayload",
    "ReadinessPortfolioDirectory",
    "ReadinessReport",
    "ReadinessSeverity",
    "build_readiness_report",
]

_PORTFOLIO_REPORT_LIMIT = 100


class _ReadinessUnavailableError(RuntimeError):
    """The report cannot be built; carry the failed component."""

    def __init__(self, component: ComponentReport) -> None:
        """Store the component for the failed envelope."""
        self.component = component
        super().__init__(component.detail)


async def build_readiness_report(
    *,
    portfolio: PortfolioService,
    execution: ExecutionStore | None,
    risk_policies: RiskPolicyStore | None,
    portfolios: ReadinessPortfolioDirectory | None,
    deployment_id: UUID | None = None,
    portfolio_id: UUID | None = None,
    futures_account: FuturesAccountSnapshotStore | None = None,
    fleet_entries: FleetEntriesPayload | None = None,
) -> ReadinessReport:
    """Build the advisory preflight for one deployment, one portfolio, or the fleet.

    ``futures_account`` adds the CFM futures section (ADR 0127) when a mirror snapshot
    exists. ``fleet_entries`` is the fleet entry readiness section (ADR 0130); it is graded
    as its own ``fleet_entries`` component in every scope, since a fleet-wide block stops
    the scoped books too.
    """
    now = datetime.now(UTC)
    warnings: list[str] = []
    findings: list[ReadinessFinding] = []
    try:
        deployments = await _list_deployments(execution)
        scope, scoped, portfolio_ids = await _resolve_scope(
            deployments, portfolios, deployment_id, portfolio_id, warnings
        )
    except _ReadinessUnavailableError as error:
        return _failed_report(now, error.component)
    all_snapshots = await _load_snapshots(execution, deployments, findings)
    snapshots = {item.id: all_snapshots[item.id] for item in scoped if item.id in all_snapshots}
    live_deployments = tuple(item for item in deployments if item.mode is DeploymentMode.LIVE)
    live_snapshots = {
        item.id: all_snapshots[item.id] for item in live_deployments if item.id in all_snapshots
    }
    live_bearing = risk_bearing_snapshots(tuple(live_snapshots.values()), DeploymentMode.LIVE)
    policy = await _load_policy(risk_policies, findings)
    venue = await _read_venue(portfolio, policy, all_snapshots, findings)
    account = (
        None
        if policy is None
        else _account_section(
            policy,
            live_bearing,
            venue,
            findings,
            inventory_evidence=_inventory_evidence(
                live_deployments, live_snapshots, quote=policy.definition.quote_currency
            ),
        )
    )
    aggregates = await _load_portfolios(portfolios, portfolio_ids, warnings)
    rows = tuple(
        _deployment_row(snapshot, _aggregate_for(aggregates, snapshot.deployment.portfolio_id))
        for snapshot in snapshots.values()
    )
    portfolio_sections = [
        await _portfolio_section(
            portfolios,
            aggregate,
            snapshots=all_snapshots,
            deployments=deployments,
            account=account,
            warnings=warnings,
        )
        for aggregate in aggregates
    ]
    fee_evidence = await _fee_evidence(portfolio, snapshots, findings)
    paper = _paper_section(policy, scoped, warnings)
    futures = await futures_section(futures_account, findings)
    if warnings:
        findings.append(
            ReadinessFinding(
                reason_code="READINESS_SCOPE_INCOMPLETE",
                severity=ReadinessSeverity.UNKNOWN,
                detail=(
                    "Portfolio, runtime, or quote scope is incomplete; see partial_result_warnings."
                ),
            )
        )
    _scope_findings(rows, account, portfolio_sections, paper, findings)
    components = _components(findings, venue=venue, fee_evidence=fee_evidence, warnings=warnings)
    if fleet_entries is not None:
        components.append(fleet_entries_component(fleet_entries))
    modes = tuple(mode for mode in ("live", "paper") if any(row.mode == mode for row in rows))
    return ReadinessReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=PORTFOLIO_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=ReadinessPayload(
            scope=scope,
            deployment_id=deployment_id,
            portfolio_id=portfolio_id,
            modes_in_scope=modes,
            inventory=_inventory_evidence(scoped, snapshots),
            portfolio_scope_complete=not warnings,
            venue_quotes=venue.rows,
            account=account,
            paper=paper,
            deployments=rows,
            portfolios=tuple(portfolio_sections),
            fee_evidence=fee_evidence,
            futures=futures,
            findings=tuple(findings),
            fleet_entries=fleet_entries,
        ),
    )


def _failed_report(now: datetime, component: ComponentReport) -> ReadinessReport:
    """Assemble the failed envelope without guessing any capacity."""
    return ReadinessReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.FAILED,
        components=(component,),
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action((component,)),
        payload=ReadinessPayload(scope="fleet"),
    )


async def _list_deployments(execution: ExecutionStore | None) -> tuple[Deployment, ...]:
    """List every deployment, failing the report when storage errors."""
    if execution is None or isinstance(execution, DisabledExecutionStore):
        raise _ReadinessUnavailableError(
            ComponentReport(
                name="readiness",
                status=ReportStatus.FAILED,
                reason_code="EXECUTION_UNAVAILABLE",
                detail="No execution store is attached; the managed fleet is unknown, not empty.",
            )
        )
    try:
        return await execution.list_deployments()
    except Exception as error:
        raise _ReadinessUnavailableError(
            ComponentReport(
                name="readiness",
                status=ReportStatus.FAILED,
                reason_code="EXECUTION_UNAVAILABLE",
                detail="Execution storage could not be read; readiness is unavailable.",
            )
        ) from error


async def _resolve_scope(
    deployments: tuple[Deployment, ...],
    portfolios: ReadinessPortfolioDirectory | None,
    deployment_id: UUID | None,
    portfolio_id: UUID | None,
    warnings: list[str],
) -> tuple[Literal["deployment", "portfolio", "fleet"], tuple[Deployment, ...], tuple[UUID, ...]]:
    """Pick the scoped books and the portfolio sections the scope needs."""
    if deployment_id is not None:
        match = next((item for item in deployments if item.id == deployment_id), None)
        if match is None:
            raise _ReadinessUnavailableError(
                ComponentReport(
                    name="readiness",
                    status=ReportStatus.FAILED,
                    reason_code="DEPLOYMENT_NOT_FOUND",
                    detail="No deployment exists for that id.",
                )
            )
        portfolio_ids = () if match.portfolio_id is None else (match.portfolio_id,)
        return "deployment", (match,), portfolio_ids
    if portfolio_id is not None:
        if portfolios is None:
            raise _ReadinessUnavailableError(
                ComponentReport(
                    name="readiness",
                    status=ReportStatus.FAILED,
                    reason_code="PORTFOLIO_STORAGE_UNAVAILABLE",
                    detail="Portfolio scope requires portfolio storage (PostgreSQL).",
                )
            )
        return "portfolio", members(deployments, portfolio_id), (portfolio_id,)
    portfolio_ids = await _fleet_portfolio_ids(portfolios, warnings)
    return "fleet", deployments, portfolio_ids


async def _fleet_portfolio_ids(
    portfolios: ReadinessPortfolioDirectory | None, warnings: list[str]
) -> tuple[UUID, ...]:
    """List fleet portfolio ids, warning when storage or the page bound hides some."""
    if portfolios is None:
        warnings.append("Portfolio storage is unavailable; portfolio caps and scope are unknown.")
        return ()
    try:
        page = await portfolios.list_page(limit=_PORTFOLIO_REPORT_LIMIT, offset=0)
    except Exception:  # noqa: BLE001 - portfolio listing degrades to books only.
        warnings.append("Portfolios could not be listed; portfolio caps are omitted.")
        return ()
    if page.total > len(page.portfolios):
        warnings.append(f"Showing the first {len(page.portfolios)} of {page.total} portfolios.")
    return tuple(item.portfolio.portfolio_id for item in page.portfolios)


def _record_unreadable_snapshots(
    deployments: tuple[Deployment, ...], findings: list[ReadinessFinding]
) -> None:
    """Record every book as unknown when no execution store is attached."""
    findings.extend(_unreadable_snapshot(deployment.id) for deployment in deployments)


def _unreadable_snapshot(deployment_id: UUID) -> ReadinessFinding:
    """One unknown finding for a book whose inventory could not be read."""
    return ReadinessFinding(
        reason_code="SNAPSHOT_UNAVAILABLE",
        severity=ReadinessSeverity.UNKNOWN,
        deployment_id=deployment_id,
        detail=(
            "Inventory and working orders could not be read; this book's "
            "exposure is unknown, not zero."
        ),
    )


async def _load_snapshots(
    execution: ExecutionStore | None,
    deployments: tuple[Deployment, ...],
    findings: list[ReadinessFinding],
) -> dict[UUID, DeploymentSnapshot]:
    """Load full snapshots, recording an unknown finding for each unreadable book."""
    if execution is None:
        _record_unreadable_snapshots(deployments, findings)
        return {}
    snapshots: dict[UUID, DeploymentSnapshot] = {}
    for deployment in deployments:
        try:
            snapshot = await execution.get_accounting_snapshot(deployment.id)
            snapshots[deployment.id] = snapshot
            if _inventory_unresolved(snapshot):
                findings.append(
                    ReadinessFinding(
                        reason_code="BOOK_ACCOUNTING_UNRESOLVED",
                        severity=ReadinessSeverity.UNKNOWN,
                        deployment_id=deployment.id,
                        detail=(
                            "Retained fills/executions, occupied runtimes without positions, or "
                            "incomplete accounting scope leave managed inventory unresolved; "
                            "no flatness or exact capacity is proved."
                        ),
                    )
                )
        except Exception:  # noqa: BLE001 - one unreadable book must not sink the report.
            findings.append(_unreadable_snapshot(deployment.id))
    return snapshots


async def _load_policy(
    risk_policies: RiskPolicyStore | None, findings: list[ReadinessFinding]
) -> ActiveRiskPolicy | None:
    """Load the effective policy, degrading to unknown caps when the store fails."""
    try:
        return await load_effective_policy(risk_policies)
    except Exception:  # noqa: BLE001 - policy store failures degrade the report.
        findings.append(
            ReadinessFinding(
                reason_code="POLICY_UNAVAILABLE",
                severity=ReadinessSeverity.UNKNOWN,
                detail="The effective risk policy could not be loaded; caps are unknown.",
            )
        )
        return None


def _components(
    findings: Sequence[ReadinessFinding],
    *,
    venue: _VenueRead,
    fee_evidence: ReadinessFeeEvidence,
    warnings: Sequence[str],
) -> list[ComponentReport]:
    """Grade the report from finding severities plus venue and fee evidence."""
    severities = {finding.severity for finding in findings}
    if ReadinessSeverity.VIOLATION in severities:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_VIOLATION",
            detail="Current exposure exceeds at least one cap; inspect before new risk.",
        )
    elif ReadinessSeverity.UNKNOWN in severities or warnings:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_UNKNOWN",
            detail="At least one capacity input is unknown; nothing was guessed.",
        )
    elif ReadinessSeverity.ADVISORY in severities:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_ADVISORY",
            detail=(
                "Advisory findings only (allocation overcommitment, optimistic fee "
                "assumptions, or latched breakers); no policy was changed."
            ),
        )
    else:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail="No readiness findings; caps and capacities are advisory disclosure.",
        )
    components = [main]
    if not venue.complete:
        detail = (
            "The exchange account could not be queried."
            if venue.failure is None
            else venue.failure.summary()
        )
        components.append(
            ComponentReport(
                name="venue",
                status=ReportStatus.DEGRADED,
                reason_code="EXCHANGE_UNAVAILABLE",
                detail=detail,
            )
        )
    elif venue.demo:
        components.append(
            ComponentReport(
                name="venue",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE",
                detail="Venue balances are demo data, not account evidence.",
            )
        )
    if fee_evidence.unavailable_reason == "read_failure":
        components.append(
            ComponentReport(
                name="fees",
                status=ReportStatus.DEGRADED,
                reason_code="FEE_EVIDENCE_UNAVAILABLE",
                detail="Account fee evidence could not be read; assumptions were not judged.",
            )
        )
    elif fee_evidence.demo:
        components.append(
            ComponentReport(
                name="fees",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE",
                detail="Fee evidence is demo data; paper assumptions were not judged.",
            )
        )
    return components
