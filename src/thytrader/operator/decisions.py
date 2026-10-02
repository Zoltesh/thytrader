"""Build the read-only operator ``decisions`` report from the per-bar decision journal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader import __version__
from thytrader.execution.decision_store import (
    DecisionStoreError,
    DisabledDecisionJournalStore,
    decision_storage_label,
)
from thytrader.execution.decisions import (
    DECISION_PAGE_MAX_LIMIT,
    DECISION_RETENTION_MAX_AGE,
    DECISION_RETENTION_MAX_ROWS_PER_DEPLOYMENT,
    DecisionPage,
)
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    DecisionsPayload,
    DecisionsReport,
    ReportStatus,
)
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.decisions import DecisionOutcome


async def decisions_report(
    store: DecisionJournalStore | None,
    *,
    deployment_id: UUID | None = None,
    strategy_id: UUID | None = None,
    outcomes: Sequence[DecisionOutcome] = (),
    limit: int = 50,
    cursor: str | None = None,
) -> DecisionsReport:
    """Return one newest-first page of per-bar decisions for a bot, a strategy, or all bots."""
    journal = store or DisabledDecisionJournalStore()
    label = decision_storage_label(journal)
    bounded = max(1, min(limit, DECISION_PAGE_MAX_LIMIT))
    page = DecisionPage(decisions=())
    warnings: list[str] = []
    try:
        page = await _page(
            journal,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            outcomes=outcomes,
            limit=bounded,
            cursor=cursor,
        )
    except DecisionStoreError as error:
        component = ComponentReport(
            name="decisions",
            status=ReportStatus.FAILED,
            reason_code="DECISION_JOURNAL_READ_FAILED",
            detail=str(error)[:500],
        )
    else:
        component = _component(label, len(page.decisions))
    if label == "unavailable":
        warnings.append("Decision journal storage is unavailable; per-bar reads are empty.")
    components = (component,)
    return DecisionsReport(
        application_version=__version__,
        generated_at=datetime.now(UTC),
        overall_status=aggregate_status(components),
        components=components,
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=DecisionsPayload(
            storage=label,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            outcomes=tuple(outcomes),
            decisions=page.decisions,
            next_cursor=page.next_cursor,
            retention_max_rows_per_deployment=DECISION_RETENTION_MAX_ROWS_PER_DEPLOYMENT,
            retention_max_age_days=DECISION_RETENTION_MAX_AGE.days,
        ),
    )


async def _page(
    journal: DecisionJournalStore,
    *,
    deployment_id: UUID | None,
    strategy_id: UUID | None,
    outcomes: Sequence[DecisionOutcome],
    limit: int,
    cursor: str | None,
) -> DecisionPage:
    """Read the deployment, strategy, or all-bots page the filters select."""
    if deployment_id is not None:
        return await journal.list_for_deployment(
            deployment_id, limit=limit, cursor=cursor, outcomes=outcomes
        )
    if strategy_id is not None:
        return await journal.list_for_strategy(
            strategy_id, limit=limit, cursor=cursor, outcomes=outcomes
        )
    return await journal.list_recent(limit=limit, cursor=cursor, outcomes=outcomes)


def _component(label: str, returned: int) -> ComponentReport:
    """Healthy with a durable journal; failed (explained) without one."""
    if label == "available":
        return ComponentReport(
            name="decisions",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"{returned} per-bar decision row(s) on this page.",
        )
    return ComponentReport(
        name="decisions",
        status=ReportStatus.FAILED,
        reason_code="DECISION_STORAGE_UNAVAILABLE",
        detail="The API has no durable decision journal; per-bar decisions are empty.",
    )
