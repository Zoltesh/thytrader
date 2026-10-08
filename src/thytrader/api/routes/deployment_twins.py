"""Paper/live twin pairing HTTP contracts for deployments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_strategy_snapshot_store,
)
from thytrader.api.routes.deployment_models import DeploymentTwinResponse, LinkTwinRequest
from thytrader.api.routes.deployments import require_deployment_row
from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.strategies.snapshots import StrategySnapshotError, StrategySnapshotStore
from thytrader.trading.models import ExecutionStoreError
from thytrader.trading.store import ExecutionStore
from thytrader.trading.twins import TwinConflictError, TwinValidationError, load_twin_snapshots

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


@router.get("/{deployment_id}/twin", response_model=DeploymentTwinResponse)
async def get_deployment_twin(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> DeploymentTwinResponse:
    """Read the deliberate comparison pairing, with no exchange requests."""
    await require_deployment_row(store, deployment_id)
    try:
        link = await store.get_twin_link(deployment_id)
    except ExecutionStoreError as error:
        raise _twin_http_error(error) from None
    return DeploymentTwinResponse(deployment_id=deployment_id, twin=link)


@router.put("/{deployment_id}/twin", response_model=DeploymentTwinResponse)
async def link_deployment_twin(
    deployment_id: UUID,
    request: LinkTwinRequest,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
) -> DeploymentTwinResponse:
    """Save a comparable one-to-one pairing without deployment or order authority."""
    first = await require_deployment_row(store, deployment_id)
    second = await require_deployment_row(store, request.counterpart_deployment_id)
    try:
        snapshots = await load_twin_snapshots(first, second, publications)
        link = await store.link_twins(
            deployment_id, request.counterpart_deployment_id, snapshots=snapshots
        )
    except (ExecutionStoreError, TwinConflictError, TwinValidationError) as error:
        raise _twin_http_error(error) from None
    except StrategySnapshotError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twin strategy snapshots could not be verified.",
        ) from None
    await _append_twin_audit(
        audit, "link_deployment_twins", deployment_id, request.counterpart_deployment_id
    )
    return DeploymentTwinResponse(deployment_id=deployment_id, twin=link)


@router.delete("/{deployment_id}/twin", response_model=DeploymentTwinResponse)
async def unlink_deployment_twin(
    deployment_id: UUID,
    counterpart_deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentTwinResponse:
    """Remove only the expected partner, preserving a replacement on stale requests."""
    await require_deployment_row(store, deployment_id)
    try:
        await store.unlink_twins(deployment_id, counterpart_deployment_id)
    except (ExecutionStoreError, TwinConflictError) as error:
        raise _twin_http_error(error) from None
    await _append_twin_audit(
        audit, "unlink_deployment_twins", deployment_id, counterpart_deployment_id
    )
    return DeploymentTwinResponse(deployment_id=deployment_id, twin=None)


def _twin_http_error(
    error: ExecutionStoreError | TwinConflictError | TwinValidationError,
) -> HTTPException:
    """Map typed validation/conflict failures and redacted storage errors."""
    if isinstance(error, TwinValidationError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, TwinConflictError):
        code = status.HTTP_409_CONFLICT
    elif "not found" in str(error).lower():
        code = status.HTTP_404_NOT_FOUND
    else:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=code, detail=str(error))


async def _append_twin_audit(
    audit: AuditEventStore, action: str, deployment_id: UUID, counterpart_id: UUID
) -> None:
    """Record metadata control with identifiers only, following runtime audit policy."""
    await audit.append(
        AuditEvent(
            occurred_at=datetime.now(UTC),
            category=AuditEventCategory.RUNTIME,
            action=action,
            outcome=AuditEventOutcome.SUCCESS,
            detail=f"deployment_id={deployment_id} counterpart_deployment_id={counterpart_id}",
        )
    )
