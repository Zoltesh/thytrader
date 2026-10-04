"""Versioned frozen-campaign and read-only economic preflight surfaces."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from thytrader.api.dependencies import get_campaign_service as campaign_service
from thytrader.execution.economics import (
    EconomicPreflight,
    EconomicPreflightRequest,
    economic_preflight,
)
from thytrader.research.campaign_export import campaign_csv
from thytrader.research.campaign_service import CampaignService
from thytrader.research.campaigns import CampaignRecord, CampaignStart

router = APIRouter(prefix="/api/v1/research", tags=["research campaigns"])


@router.post("/economics", response_model=EconomicPreflight)
def preflight(request: EconomicPreflightRequest) -> EconomicPreflight:
    """Read-only calculation; supplied assumptions confer no order or deployment authority."""
    try:
        return economic_preflight(request)
    except ValueError as error:
        raise HTTPException(
            422, detail={"code": "economics_invalid", "message": str(error)}
        ) from None


@router.post("/campaigns", response_model=CampaignRecord, status_code=201)
async def create_campaign(
    start: CampaignStart, service: Annotated[CampaignService, Depends(campaign_service)]
) -> CampaignRecord:
    """Freeze and authorize research children; prospective cases wait for verified data."""
    try:
        return await service.create(start)
    except ValueError as error:
        raise HTTPException(
            422, detail={"code": "campaign_invalid", "message": str(error)}
        ) from None


@router.get("/campaigns", response_model=tuple[CampaignRecord, ...])
async def list_campaigns(
    service: Annotated[CampaignService, Depends(campaign_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> tuple[CampaignRecord, ...]:
    """Read bounded native campaign manifests and current child evidence."""
    return await service.store.list(limit=limit)


@router.get("/campaigns/{campaign_id}", response_model=CampaignRecord)
async def get_campaign(
    campaign_id: UUID, service: Annotated[CampaignService, Depends(campaign_service)]
) -> CampaignRecord:
    """Read one persistent frozen campaign after a process or worker restart."""
    try:
        return await service.store.get(campaign_id)
    except KeyError:
        raise HTTPException(
            404, detail={"code": "campaign_not_found", "message": "Campaign was not found."}
        ) from None


@router.post("/campaigns/{campaign_id}/refresh", response_model=CampaignRecord)
async def refresh_campaign(
    campaign_id: UUID, service: Annotated[CampaignService, Depends(campaign_service)]
) -> CampaignRecord:
    """Advance approved research only; concurrent refreshes queue each child once."""
    try:
        return await service.refresh(campaign_id)
    except KeyError:
        raise HTTPException(
            404, detail={"code": "campaign_not_found", "message": "Campaign was not found."}
        ) from None


@router.get("/campaigns/{campaign_id}/export", response_class=PlainTextResponse)
async def export_campaign(
    campaign_id: UUID, service: Annotated[CampaignService, Depends(campaign_service)]
) -> PlainTextResponse:
    """Download a bounded CSV covering every frozen case, including pending validation."""
    record = await get_campaign(campaign_id, service)
    return PlainTextResponse(
        campaign_csv(record),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="campaign-{campaign_id}.csv"'},
    )
