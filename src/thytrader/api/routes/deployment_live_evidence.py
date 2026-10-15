"""Live-start evidence: assessed and recorded with every live start, read back later (ADR 0143).

A live start (single bot or adopt-into-bot) gathers what its exact strategy fingerprint has
shown, warns with ``LIVE_EVIDENCE_THIN`` when that is below the risk policy's thresholds, and
records it with the new bot. Only the opt-in ``require_live_evidence`` safeguard refuses
(409 ``LIVE_EVIDENCE_REQUIRED``), before anything is created.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from thytrader.api.dependencies import get_execution_store
from thytrader.api.live_evidence_wiring import get_live_evidence_store
from thytrader.research.live_evidence import (
    LiveStartEvidence,
    gather_live_start_evidence,
    live_evidence_message,
)
from thytrader.research.live_evidence_store import (
    LiveEvidenceStore,
    LiveEvidenceStoreUnavailableError,
)
from thytrader.risk.start_safeguards import StartSafeguards
from thytrader.risk.store import load_effective_policy
from thytrader.trading.ids import utc_now
from thytrader.trading.models import ExecutionStoreError
from thytrader.trading.store import ExecutionStore

if TYPE_CHECKING:
    from thytrader.research.catalog import ResearchStudyCatalog
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.snapshots import StrategySnapshot

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])

NOT_RECORDED = "LIVE_EVIDENCE_NOT_RECORDED"


async def assess_live_start(
    snapshot: StrategySnapshot,
    *,
    risk_store: RiskPolicyStore,
    catalog: ResearchStudyCatalog | None,
    execution: ExecutionStore,
    warnings: list[str],
) -> LiveStartEvidence:
    """Gather the snapshot's evidence; warn when thin, or refuse under the opt-in safeguard."""
    policy = await load_effective_policy(risk_store)
    safeguards = policy.definition.start_safeguards or StartSafeguards()
    evidence = await gather_live_start_evidence(
        strategy_fingerprint=snapshot.strategy_fingerprint,
        strategy_id=snapshot.definition.strategy_id,
        catalog=catalog,
        execution=execution,
        thresholds=safeguards.live_evidence_thresholds,
        now=utc_now(),
    )
    if evidence.thin:
        if safeguards.require_live_evidence:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=live_evidence_message(evidence, refused=True),
            )
        warnings.append(live_evidence_message(evidence, refused=False))
    return evidence


async def record_live_start(
    store: LiveEvidenceStore,
    deployment_id: UUID,
    evidence: LiveStartEvidence,
    *,
    warnings: list[str],
) -> None:
    """Keep the evidence with the started bot; a storage failure warns, never undoes it."""
    try:
        await store.record(deployment_id, evidence)
    except LiveEvidenceStoreUnavailableError:
        warnings.append(
            f"{NOT_RECORDED}: the bot started, but its live-start evidence could not be "
            "stored; it is still in this response."
        )


@router.get("/{deployment_id}/live-evidence", response_model=LiveStartEvidence)
async def get_live_evidence(
    deployment_id: UUID,
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    store: Annotated[LiveEvidenceStore, Depends(get_live_evidence_store)],
) -> LiveStartEvidence:
    """The evidence recorded when this live bot started (404 when none was)."""
    try:
        await execution.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    try:
        evidence = await store.get(deployment_id)
    except LiveEvidenceStoreUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live-start evidence storage is unavailable.",
        ) from None
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No live-start evidence is recorded for this deployment: it is a paper bot, "
                "or it started live before ADR 0143."
            ),
        )
    return evidence
