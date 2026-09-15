"""HTTP contract for the versioned risk-policy registry."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from thytrader.api.dependencies import get_audit_event_store, get_risk_policy_store
from thytrader.execution.ids import utc_now
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.risk.models import (
    ActiveRiskPolicy,
    CapitalAllocation,
    RiskPolicyWrite,
)
from thytrader.risk.service import publish_risk_policy
from thytrader.risk.store import RiskPolicyStore, RiskPolicyStoreError, load_effective_policy

router = APIRouter(prefix="/api/v1/risk-policy", tags=["risk-policy"])


class AllocationBody(BaseModel):
    """One strategy capital reservation in a publish request."""

    strategy_id: str
    allocated_quote: str


class RiskPolicyWriteBody(BaseModel):
    """Operator-authored policy fields without identity or fingerprint."""

    product_allowlist: tuple[str, ...] = ()
    max_concurrent_running_deployments: int = Field(ge=1, le=32)
    max_concurrent_open_positions: int = Field(ge=1, le=32)
    max_portfolio_exposure_fraction: str
    per_product_max_exposure_fraction: str
    paper_capital_quote: str
    allocations: tuple[AllocationBody, ...] = ()


class RiskPolicyResponse(BaseModel):
    """Effective policy plus identity metadata."""

    source: str
    policy_fingerprint: str
    schema_version: str
    policy_id: str
    version: int
    quote_currency: str
    product_allowlist: tuple[str, ...]
    max_concurrent_running_deployments: int
    max_concurrent_open_positions: int
    max_portfolio_exposure_fraction: str
    per_product_max_exposure_fraction: str
    paper_capital_quote: str
    allocations: tuple[AllocationBody, ...]


@router.get("", response_model=RiskPolicyResponse)
async def get_risk_policy(
    store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
) -> RiskPolicyResponse:
    """Return the compiled default or the active published policy."""
    active = await load_effective_policy(store)
    return _response(active)


@router.put("", response_model=RiskPolicyResponse)
async def put_risk_policy(
    body: RiskPolicyWriteBody,
    store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> RiskPolicyResponse:
    """Publish a new immutable policy version after validation."""
    try:
        write = _write_from_body(body)
        active = await publish_risk_policy(store, write)
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from None
    except RiskPolicyStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    await audit.append(
        AuditEvent(
            occurred_at=utc_now(),
            category=AuditEventCategory.RUNTIME,
            action="set_risk_policy",
            outcome=AuditEventOutcome.SUCCESS,
            detail=f"Published risk policy {active.policy_fingerprint}.",
        )
    )
    return _response(active)


def _write_from_body(body: RiskPolicyWriteBody) -> RiskPolicyWrite:
    """Map the HTTP body onto the frozen write model."""
    allocations = tuple(
        CapitalAllocation(strategy_id=UUID(item.strategy_id), allocated_quote=item.allocated_quote)
        for item in body.allocations
    )
    return RiskPolicyWrite(
        product_allowlist=body.product_allowlist,
        max_concurrent_running_deployments=body.max_concurrent_running_deployments,
        max_concurrent_open_positions=body.max_concurrent_open_positions,
        max_portfolio_exposure_fraction=body.max_portfolio_exposure_fraction,
        per_product_max_exposure_fraction=body.per_product_max_exposure_fraction,
        paper_capital_quote=body.paper_capital_quote,
        allocations=allocations,
    )


def _response(active: ActiveRiskPolicy) -> RiskPolicyResponse:
    """Serialize one effective policy."""
    definition = active.definition
    return RiskPolicyResponse(
        source=active.source.value,
        policy_fingerprint=active.policy_fingerprint,
        schema_version=definition.schema_version,
        policy_id=str(definition.policy_id),
        version=definition.version,
        quote_currency=definition.quote_currency,
        product_allowlist=definition.product_allowlist,
        max_concurrent_running_deployments=definition.max_concurrent_running_deployments,
        max_concurrent_open_positions=definition.max_concurrent_open_positions,
        max_portfolio_exposure_fraction=definition.max_portfolio_exposure_fraction,
        per_product_max_exposure_fraction=definition.per_product_max_exposure_fraction,
        paper_capital_quote=definition.paper_capital_quote,
        allocations=tuple(
            AllocationBody(strategy_id=str(item.strategy_id), allocated_quote=item.allocated_quote)
            for item in definition.allocations
        ),
    )
