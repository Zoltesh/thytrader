"""Browser-facing strategy draft and immutable-publication HTTP contracts."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from thytrader.api.dependencies import get_strategy_publication_store
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyPublicationError,
    StrategyPublicationStore,
)

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


class StrategyDraftResponse(BaseModel):
    """One server-identified ephemeral strategy draft safe for browser editing."""

    strategy: StrategyDefinition


class StrategyPublishRequest(BaseModel):
    """One complete ephemeral draft supplied for immutable publication."""

    strategy: StrategyDefinition


class StrategyPublishResponse(BaseModel):
    """One immutable strategy version returned after authoritative persistence."""

    strategy_fingerprint: str
    strategy: StrategyDefinition


@router.post("", response_model=StrategyDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy_draft() -> StrategyDraftResponse:
    """Create the conservative reference draft without persistence or trading authority."""
    return StrategyDraftResponse(strategy=create_reference_draft())


@router.post(
    "/{strategy_id}/publish",
    response_model=StrategyPublishResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_strategy(
    strategy_id: UUID,
    request: StrategyPublishRequest,
    store: Annotated[StrategyPublicationStore, Depends(get_strategy_publication_store)],
) -> StrategyPublishResponse:
    """Validate and persist one browser draft as an immutable research artifact."""
    draft = request.strategy
    if draft.strategy_id != strategy_id or draft.status is not StrategyStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only the matching ephemeral draft can be published.",
        )
    published_definition = StrategyDefinition.model_validate(
        {**draft.model_dump(mode="python"), "status": StrategyStatus.PUBLISHED}
    )
    try:
        published = await store.publish(published_definition)
    except StrategyPublicationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication is unavailable.",
        ) from None
    _require_exact_publication(published, published_definition)
    return StrategyPublishResponse(
        strategy_fingerprint=published.strategy_fingerprint,
        strategy=published.definition,
    )


def _require_exact_publication(
    published: PublishedStrategy,
    expected: StrategyDefinition,
) -> None:
    """Reject a storage boundary that returns mismatched immutable strategy evidence."""
    if published.definition != expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication is unavailable.",
        )
