"""Read-only execution-quality HTTP contracts (ADR 0116).

``GET /api/v1/deployments/{id}/execution-quality`` folds one book's recorded fills
into closed round trips with exact fee, net-PnL, and journaled-close slippage
evidence against completed intent-bar closes. The twin route compares the two
books of one explicit twin link on that evidence. Both are read-only: they never
place, cancel, or mutate orders, fills, positions, or deployment state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from thytrader.api.dependencies import (
    get_decision_journal_store,
    get_execution_store,
    get_strategy_snapshot_store,
)
from thytrader.execution.decision_store import (
    DecisionJournalStore,
    DecisionStoreError,
    decision_storage_label,
)
from thytrader.execution.execution_quality import (
    ExecutionQualityEvidenceReason,
    ExecutionQualityReport,
    ExecutionTwinComparison,
    JournaledCloseEvidence,
    JournaledDecisionClose,
    build_execution_quality_report,
    build_execution_twin_comparison,
    load_journaled_close_evidence,
)
from thytrader.strategies.snapshots import StrategySnapshotError, StrategySnapshotStore
from thytrader.trading.models import DeploymentSnapshot, ExecutionStoreError
from thytrader.trading.store import ExecutionStore
from thytrader.trading.twins import TwinValidationError, load_twin_snapshots

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.strategies.snapshots import StrategySnapshot
    from thytrader.trading.twins import DeploymentTwinLink

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["execution-quality"])


@dataclass(frozen=True, slots=True)
class _ResolvedJournal:
    """Journaled-close evidence resolved for one snapshot, with route-level reasons."""

    closes: dict[tuple[str, datetime], JournaledDecisionClose]
    coverage: tuple[datetime, datetime] | None
    rows_fetched: int
    extra_reasons: tuple[ExecutionQualityEvidenceReason, ...]


@router.get(
    "/deployments/{deployment_id}/execution-quality",
    response_model=ExecutionQualityReport,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Unknown deployment."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Execution storage unavailable."},
    },
)
async def get_execution_quality(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    decisions: Annotated[DecisionJournalStore, Depends(get_decision_journal_store)],
) -> ExecutionQualityReport:
    """Return one book's read-only round-trip cost and execution-quality evidence."""
    snapshot = await _load_snapshot(store, deployment_id)
    evidence = await _journal_evidence(decisions, deployment_id, snapshot)
    return await asyncio.to_thread(
        build_execution_quality_report,
        snapshot,
        journaled_closes=evidence.closes,
        decision_coverage=evidence.coverage,
        decision_rows_fetched=evidence.rows_fetched,
        extra_reasons=evidence.extra_reasons,
    )


@router.get(
    "/deployments/{deployment_id}/execution-quality/twin",
    response_model=ExecutionTwinComparison,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Unknown deployment or no twin link."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Execution storage unavailable."},
    },
)
async def get_execution_twin_comparison(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    decisions: Annotated[DecisionJournalStore, Depends(get_decision_journal_store)],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
) -> ExecutionTwinComparison:
    """Compare one explicitly linked paper/live pair on recorded execution evidence."""
    link = await _load_twin_link(store, deployment_id)
    paper_snapshot = await _load_snapshot(store, link.paper_deployment_id)
    live_snapshot = await _load_snapshot(store, link.live_deployment_id)
    proof = await _rule_proof(paper_snapshot, live_snapshot, publications)
    paper_report = await _quality_report(decisions, paper_snapshot)
    live_report = await _quality_report(decisions, live_snapshot)
    try:
        return await asyncio.to_thread(
            build_execution_twin_comparison,
            link=link,
            paper_snapshot=paper_snapshot,
            paper_report=paper_report,
            live_snapshot=live_snapshot,
            live_report=live_report,
            strategy_snapshots=proof,
        )
    except ValueError as error:
        logger.warning("Execution-quality twin inputs disagree: %s", type(error).__name__)
        raise _unavailable() from None


async def _rule_proof(
    paper: DeploymentSnapshot,
    live: DeploymentSnapshot,
    publications: StrategySnapshotStore,
) -> tuple[StrategySnapshot, StrategySnapshot] | None:
    """Load ADR 0105 pinned rules; missing proof produces cannot-compare, not trust."""
    try:
        return await load_twin_snapshots(paper.deployment, live.deployment, publications)
    except (StrategySnapshotError, TwinValidationError) as error:
        logger.warning("Execution-quality twin rule proof unavailable: %s", type(error).__name__)
        return None


async def _quality_report(
    decisions: DecisionJournalStore, snapshot: DeploymentSnapshot
) -> ExecutionQualityReport:
    """Build one book's report with its journaled-close evidence attached."""
    evidence = await _journal_evidence(decisions, snapshot.deployment.id, snapshot)
    return await asyncio.to_thread(
        build_execution_quality_report,
        snapshot,
        journaled_closes=evidence.closes,
        decision_coverage=evidence.coverage,
        decision_rows_fetched=evidence.rows_fetched,
        extra_reasons=evidence.extra_reasons,
    )


async def _load_snapshot(store: ExecutionStore, deployment_id: UUID) -> DeploymentSnapshot:
    """Load one full snapshot, mapping missing books and outages to 404/503."""
    try:
        snapshot = await store.get_deployment(deployment_id)
    except ExecutionStoreError as error:
        if "not found" in str(error).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "deployment_not_found", "message": "Deployment was not found."},
            ) from None
        logger.warning("Execution-quality snapshot failed: %s", type(error).__name__)
        raise _unavailable() from None
    except Exception as error:  # noqa: BLE001 - redacted boundary for store faults.
        logger.warning("Execution-quality snapshot failed: %s", type(error).__name__)
        raise _unavailable() from None
    if snapshot.deployment.id != deployment_id:
        raise _unavailable()
    return snapshot


async def _load_twin_link(store: ExecutionStore, deployment_id: UUID) -> DeploymentTwinLink:
    """Read the explicit twin link; absence is a 404, outages are a 503."""
    try:
        link = await store.get_twin_link(deployment_id)
    except Exception as error:  # noqa: BLE001 - redacted boundary for store faults.
        logger.warning("Execution-quality twin link failed: %s", type(error).__name__)
        raise _unavailable() from None
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_twin_not_linked",
                "message": "No twin link is saved for this deployment.",
            },
        )
    if deployment_id not in (link.paper_deployment_id, link.live_deployment_id):
        raise _unavailable()
    return link


async def _journal_evidence(
    decisions: DecisionJournalStore, deployment_id: UUID, snapshot: DeploymentSnapshot
) -> _ResolvedJournal:
    """Read journaled closes; an unavailable journal degrades evidence, not the report."""
    unavailable = _ResolvedJournal(
        closes={},
        coverage=None,
        rows_fetched=0,
        extra_reasons=(ExecutionQualityEvidenceReason.DECISION_JOURNAL_UNAVAILABLE,),
    )
    if decision_storage_label(decisions) == "unavailable":
        return unavailable
    try:
        evidence: JournaledCloseEvidence = await load_journaled_close_evidence(
            decisions, deployment_id=deployment_id, snapshot=snapshot
        )
    except DecisionStoreError as error:
        logger.warning("Execution-quality journal read failed: %s", type(error).__name__)
        return unavailable
    extra = (
        (ExecutionQualityEvidenceReason.DECISION_COVERAGE_LIMITED,)
        if evidence.coverage_limited
        else ()
    )
    return _ResolvedJournal(
        closes=evidence.closes,
        coverage=evidence.coverage,
        rows_fetched=evidence.rows_fetched,
        extra_reasons=extra,
    )


def _unavailable() -> HTTPException:
    """Build the redacted 503 envelope for execution-quality outages."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "execution_quality_unavailable",
            "message": "Execution-quality evidence is unavailable.",
        },
    )
