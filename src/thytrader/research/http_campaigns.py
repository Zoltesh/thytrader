"""Research HTTP client for research campaigns and read-only economics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.agent_http import request_json, request_mutation_json
from thytrader.research.campaigns import CampaignStart
from thytrader.research.http_common import _encode
from thytrader.trading.economics import EconomicPreflightRequest

if TYPE_CHECKING:
    from uuid import UUID


def create_campaign(base_url: str, document: object) -> str:
    """Validate and freeze a campaign through the separately confirmed research lane."""
    request = CampaignStart.model_validate(document)
    return _encode(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/campaigns",
            payload=request.model_dump(mode="json"),
        )
    )


def economics(base_url: str, document: object) -> str:
    """Calculate read-only economics; all POSTs still cross the installation boundary."""
    request = EconomicPreflightRequest.model_validate(document)
    return _encode(
        request_mutation_json(
            method="POST",
            url=f"{base_url}/api/v1/research/economics",
            payload=request.model_dump(mode="json"),
        )
    )


def list_campaigns(base_url: str, *, limit: int = 20) -> str:
    """Read a bounded campaign page."""
    return _encode(
        request_json(method="GET", url=f"{base_url}/api/v1/research/campaigns?limit={limit}")
    )


def show_campaign(base_url: str, campaign_id: UUID) -> str:
    """Read a frozen manifest, costs, deadlines, sample gates, and child evidence."""
    return _encode(
        request_json(method="GET", url=f"{base_url}/api/v1/research/campaigns/{campaign_id}")
    )


def refresh_campaign(base_url: str, campaign_id: UUID) -> str:
    """Advance only the research jobs authorized by the frozen manifest."""
    return _encode(
        request_mutation_json(
            method="POST", url=f"{base_url}/api/v1/research/campaigns/{campaign_id}/refresh"
        )
    )
