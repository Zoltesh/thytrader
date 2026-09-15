"""Loopback HTTP helpers for orchestration status and YOLO skip audits."""

from __future__ import annotations

from thytrader.agent_http import request_json
from thytrader.agent_orchestration.models import (
    AgentOrchestrationStatus,
    SkippedConfirmationRequest,
    SkippedConfirmationResponse,
    YoloTier,
)

ORCHESTRATION_API_PREFIX = "/api/v1/agent-orchestration"


def fetch_orchestration_status(base_url: str) -> AgentOrchestrationStatus:
    """Load the running API's Safe vs YOLO advertisement."""
    payload = request_json(method="GET", url=f"{base_url}{ORCHESTRATION_API_PREFIX}")
    return AgentOrchestrationStatus.model_validate(payload)


def record_skipped_confirmation(
    base_url: str,
    *,
    tier: YoloTier,
    command: str,
) -> SkippedConfirmationResponse:
    """Persist one skipped-confirmation audit event before the mutation proceeds."""
    body = SkippedConfirmationRequest(tier=tier, command=command)
    payload = request_json(
        method="POST",
        url=f"{base_url}{ORCHESTRATION_API_PREFIX}/skipped-confirmations",
        payload=body.model_dump(mode="json"),
    )
    return SkippedConfirmationResponse.model_validate(payload)
