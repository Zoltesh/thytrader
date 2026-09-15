"""HTTP contract for playbook status and YOLO skipped-confirmation audits."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from thytrader.agent_orchestration.models import (
    AgentOrchestrationStatus,
    SkippedConfirmationRequest,
    SkippedConfirmationResponse,
)
from thytrader.agent_orchestration.service import (
    YoloSkipRejectedError,
    orchestration_status,
    record_skipped_confirmation,
)
from thytrader.api.dependencies import get_audit_event_store, get_runtime_state
from thytrader.persistence.audit_events import (
    AuditEventStore,
    AuditEventUnavailableError,
)
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.

router = APIRouter(prefix="/api/v1/agent-orchestration", tags=["agent-orchestration"])


@router.get("", response_model=AgentOrchestrationStatus)
async def get_agent_orchestration(
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> AgentOrchestrationStatus:
    """Advertise Safe vs YOLO without granting live or observation-skill authority."""
    return orchestration_status(runtime.settings)


@router.post(
    "/skipped-confirmations",
    response_model=SkippedConfirmationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_skipped_confirmation(
    body: SkippedConfirmationRequest,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    store: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> SkippedConfirmationResponse:
    """Record one YOLO-skipped `--confirm` before the CLI proceeds with a mutation."""
    try:
        return await record_skipped_confirmation(
            settings=runtime.settings,
            store=store,
            request=body,
        )
    except YoloSkipRejectedError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from None
    except AuditEventUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Audit event storage is unavailable; YOLO cannot skip --confirm.",
            },
        ) from None
