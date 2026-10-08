"""Operator ``venue-reconciliation`` report: managed books versus the whole venue (ADR 0114).

Read-only. This report compares what every managed live book claims to hold or work
against sequential venue-wide reads of balances and nonterminal spot orders. A healthy
local ledger is *not* venue reconciliation: only the venue listing can reveal drift.

Semantics that matter financially:

- External (foreign) inventory — the venue holds more of an asset than managed books
  claim — is disclosed as information, never treated as an error, and never flattened.
- A managed inventory shortfall (the venue holds *less* than managed books claim) is a
  warning: funds may have moved, or short books owe base units.
- A managed working order absent from a *complete* venue open-order listing is an
  orphan warning. Unknown is not rejected; the operator must reconcile with the venue
  before replacing or cancelling anything. This report never creates or cancels orders.
- When either managed reads or a venue listing is incomplete, every
  comparison that depends on it is reported as unknown — never guessed — and the
  report says so explicitly (fail closed).
- Observation scope, timestamps, and listing completeness are explicit. Account
  identifiers and secrets never appear; assets are named by currency only.

This module assembles and grades the report. Its models, the managed-book reads, the
venue listing reads, and the comparison rows live in the sibling
``venue_reconciliation_*`` modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    ComponentReport,
    ReportStatus,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.operator.venue_reconciliation_compare import _asset_rows, _order_section, _quote_rows
from thytrader.operator.venue_reconciliation_listing import _read_balances, _read_open_orders
from thytrader.operator.venue_reconciliation_managed import (
    _collect_inventory,
    _managed_snapshots,
    _VenueUnavailableError,
)
from thytrader.operator.venue_reconciliation_models import (
    VenueFinding,
    VenueListingEvidence,
    VenueReconciliationPayload,
    VenueReconciliationReport,
    VenueSeverity,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.portfolio.service import PortfolioService
    from thytrader.trading.store import ExecutionStore


async def build_venue_reconciliation_report(
    *,
    portfolio: PortfolioService,
    execution: ExecutionStore | None,
) -> VenueReconciliationReport:
    """Compare managed live books against fresh venue balances and open orders."""
    now = datetime.now(UTC)
    findings: list[VenueFinding] = []
    warnings: list[str] = []
    try:
        managed = await _managed_snapshots(execution, findings, warnings)
    except _VenueUnavailableError as error:
        return _failed_report(now, error.component)
    inventory = _collect_inventory(managed.snapshots)
    managed_complete = managed.evidence.status == "complete"
    balances, balance_rows = await _read_balances(portfolio, findings)
    venue_orders, orders_evidence = await _read_open_orders(portfolio, findings)
    listing_complete = balances.evidence.status == "complete"
    assets = _asset_rows(inventory, balance_rows, listing_complete, findings, managed_complete)
    quotes = _quote_rows(inventory, balance_rows, listing_complete, managed_complete)
    orders_section = _order_section(
        inventory, venue_orders, orders_evidence, findings, managed_complete
    )
    demo = balances.demo or orders_evidence.demo
    if demo:
        findings.append(
            VenueFinding(
                reason_code="DEMO_VENUE",
                severity=VenueSeverity.INFO,
                detail=(
                    "Venue listings are demo data, not account evidence; comparisons are "
                    "shown but prove nothing about the real venue."
                ),
            )
        )
    components = _components(findings, balances.evidence, orders_evidence, demo)
    return VenueReconciliationReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=PORTFOLIO_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=VenueReconciliationPayload(
            observed_at=now,
            managed_books=len(managed.snapshots) if managed_complete else None,
            managed_listing=managed.evidence,
            balances_listing=balances.evidence,
            orders_listing=orders_evidence,
            assets=assets,
            quote_currencies=quotes,
            orders=orders_section,
            findings=tuple(findings),
        ),
    )


def _failed_report(now: datetime, component: ComponentReport) -> VenueReconciliationReport:
    """Assemble a failed envelope without guessing venue state."""
    return VenueReconciliationReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.FAILED,
        components=(component,),
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action((component,)),
        payload=VenueReconciliationPayload(observed_at=now),
    )


def _components(
    findings: Sequence[VenueFinding],
    balances: VenueListingEvidence,
    orders: VenueListingEvidence,
    demo: bool,
) -> list[ComponentReport]:
    """Grade the report: warnings and unknown listings degrade; foreign holdings do not."""
    severities = {finding.severity for finding in findings}
    if VenueSeverity.WARNING in severities or VenueSeverity.UNKNOWN in severities:
        main = ComponentReport(
            name="venue_reconciliation",
            status=ReportStatus.DEGRADED,
            reason_code="VENUE_RECONCILIATION_FINDINGS",
            detail=(
                "Managed books disagree with the venue listing, or a listing is "
                "incomplete (comparisons unknown). Inspect before new risk."
            ),
        )
    else:
        main = ComponentReport(
            name="venue_reconciliation",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=(
                "Managed inventory and working orders agree with the venue listing; "
                "external holdings, if any, are disclosed as information."
            ),
        )
    components = [main]
    if balances.status == "unavailable":
        components.append(
            ComponentReport(
                name="venue_balances",
                status=ReportStatus.DEGRADED,
                reason_code="VENUE_BALANCES_LISTING_INCOMPLETE",
                detail=(
                    "The venue balance listing is incomplete; inventory comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
    else:
        components.append(
            ComponentReport(
                name="venue_balances",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE" if demo else "OK",
                detail=(
                    "Demo balance listing; not account evidence."
                    if demo
                    else f"{balances.rows} balance row(s) listed completely."
                ),
            )
        )
    if orders.status == "unavailable":
        components.append(
            ComponentReport(
                name="venue_orders",
                status=ReportStatus.DEGRADED,
                reason_code="VENUE_ORDERS_LISTING_INCOMPLETE",
                detail=(
                    "The venue open-order listing is incomplete; order comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
    else:
        components.append(
            ComponentReport(
                name="venue_orders",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE" if demo else "OK",
                detail=(
                    "Demo order listing; not account evidence."
                    if demo
                    else f"{orders.rows} nonterminal spot order(s) observed after full pagination."
                ),
            )
        )
    return components
