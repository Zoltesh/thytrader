"""Read-only per-bar decision timeline HTTP contracts (ADR 0087).

``GET /api/v1/deployments/{id}/decisions`` pages one bot's journal newest first and
``GET /api/v1/strategies/{id}/decisions`` aggregates across that strategy's bots.
Both accept repeated ``outcome`` filters and an opaque ``cursor``. A storage-free API
answers ``storage: "unavailable"`` with an empty page instead of an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from thytrader.api.dependencies import (
    get_decision_journal_store,
    get_execution_store,
    get_strategy_store,
)
from thytrader.api.strategy_http import strategy_http_error
from thytrader.execution.decision_store import (
    DecisionJournalStore,
    DecisionStoreError,
    decision_storage_label,
)
from thytrader.execution.decisions import (
    DECISION_PAGE_MAX_LIMIT,
    BarDecision,
    DecisionOutcome,
    DecisionPage,
)
from thytrader.execution.models import ExecutionStoreError
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.strategies.library import (
    StrategyLibraryError,
    StrategyStore,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

router = APIRouter(prefix="/api/v1", tags=["decisions"])

DecisionStorage = Literal["available", "unavailable"]


class DeploymentDecisionsResponse(BaseModel):
    """One newest-first page of a bot's per-bar decisions."""

    deployment_id: UUID
    decisions: tuple[BarDecision, ...]
    limit: int
    returned: int
    next_cursor: str | None = None
    storage: DecisionStorage = Field(
        description="unavailable when the API has no durable journal (empty page)."
    )


class StrategyDecisionsResponse(BaseModel):
    """One newest-first page of decisions across a strategy's bots."""

    strategy_id: UUID
    deployment_id: UUID | None = None
    decisions: tuple[BarDecision, ...]
    limit: int
    returned: int
    next_cursor: str | None = None
    storage: DecisionStorage = Field(
        description="unavailable when the API has no durable journal (empty page)."
    )


@router.get("/deployments/{deployment_id}/decisions", response_model=DeploymentDecisionsResponse)
async def list_deployment_decisions(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    decisions: Annotated[DecisionJournalStore, Depends(get_decision_journal_store)],
    limit: Annotated[int, Query(ge=1, le=DECISION_PAGE_MAX_LIMIT)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    outcome: Annotated[list[DecisionOutcome] | None, Query()] = None,
    product_id: Annotated[str | None, Query(pattern=SPOT_PRODUCT_ID_PATTERN)] = None,
) -> DeploymentDecisionsResponse:
    """Return what this bot decided on each completed bar, newest first."""
    await _require_deployment(store, deployment_id)
    page = await _read(
        decisions.list_for_deployment(
            deployment_id,
            limit=limit,
            cursor=cursor,
            outcomes=tuple(outcome or ()),
            product_id=product_id,
        )
    )
    return DeploymentDecisionsResponse(
        deployment_id=deployment_id,
        decisions=page.decisions,
        limit=limit,
        returned=len(page.decisions),
        next_cursor=page.next_cursor,
        storage=decision_storage_label(decisions),
    )


@router.get("/strategies/{strategy_id}/decisions", response_model=StrategyDecisionsResponse)
async def list_strategy_decisions(
    strategy_id: UUID,
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    decisions: Annotated[DecisionJournalStore, Depends(get_decision_journal_store)],
    limit: Annotated[int, Query(ge=1, le=DECISION_PAGE_MAX_LIMIT)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    outcome: Annotated[list[DecisionOutcome] | None, Query()] = None,
    deployment_id: Annotated[UUID | None, Query()] = None,
) -> StrategyDecisionsResponse:
    """Return decisions across this strategy's paper and live bots, newest first."""
    try:
        await strategies.get(strategy_id)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    page = await _read(
        decisions.list_for_strategy(
            strategy_id,
            limit=limit,
            cursor=cursor,
            outcomes=tuple(outcome or ()),
            deployment_id=deployment_id,
        )
    )
    return StrategyDecisionsResponse(
        strategy_id=strategy_id,
        deployment_id=deployment_id,
        decisions=page.decisions,
        limit=limit,
        returned=len(page.decisions),
        next_cursor=page.next_cursor,
        storage=decision_storage_label(decisions),
    )


async def _require_deployment(store: ExecutionStore, deployment_id: UUID) -> None:
    """404 an unknown deployment; 503 when execution storage is unavailable."""
    try:
        await store.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None


async def _read(pending: Awaitable[DecisionPage]) -> DecisionPage:
    """Await one journal page, mapping bad cursors to 400 and storage faults to 503."""
    try:
        return await pending
    except DecisionStoreError as error:
        code = (
            status.HTTP_400_BAD_REQUEST
            if "cursor" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
