"""Liveness and readiness HTTP endpoints."""

from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from thytrader import __version__
from thytrader.api.dependencies import get_runtime_state
from thytrader.ops_contract import OPS_CONTRACT_ID, expected_ops_contract

# FastAPI resolves this dependency annotation at runtime.
from thytrader.runtime import RuntimeState  # noqa: TC001

router = APIRouter(prefix="/health", tags=["health"])


class HealthOpsContract(BaseModel):
    """Full ops-contract payload advertised beside package version 0.1.0."""

    id: str
    max_historical_interval_count: int
    backtest_engines: list[str]
    paper_timeframes: list[str]
    live_timeframes: list[str]
    htf_filter_runtimes: list[str]
    indicator_timeframe_runtimes: list[str]
    position_sides: list[str]
    attached_entry_brackets: list[str]
    paper_deploy_fee_fields: list[str]
    experiential_model_engines: list[str]
    expected_schema_revision: str


class HealthResponse(BaseModel):
    """Stable process-health response exposed to operators."""

    service: Literal["api"]
    status: Literal["ok"]
    version: str
    ops_contract_id: str
    ops_contract: HealthOpsContract


class ReadinessResponse(BaseModel):
    """Stable dependency-readiness response exposed to operators."""

    service: Literal["api"]
    status: Literal["ready", "not_ready"]
    version: str
    ops_contract_id: str
    ops_contract: HealthOpsContract


def _advertised_ops_contract() -> HealthOpsContract:
    """Return this checkout's ops contract without default-filling callers."""
    return HealthOpsContract.model_validate(expected_ops_contract())


@router.get("/live", response_model=HealthResponse)
async def get_liveness() -> HealthResponse:
    """Report that the API process can serve requests."""
    return HealthResponse(
        service="api",
        status="ok",
        version=__version__,
        ops_contract_id=OPS_CONTRACT_ID,
        ops_contract=_advertised_ops_contract(),
    )


@router.get("/ready", response_model=ReadinessResponse)
async def get_readiness(
    response: Response,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> ReadinessResponse:
    """Report that API startup completed successfully."""
    if not runtime.ready:
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
        return ReadinessResponse(
            service="api",
            status="not_ready",
            version=__version__,
            ops_contract_id=OPS_CONTRACT_ID,
            ops_contract=_advertised_ops_contract(),
        )
    return ReadinessResponse(
        service="api",
        status="ready",
        version=__version__,
        ops_contract_id=OPS_CONTRACT_ID,
        ops_contract=_advertised_ops_contract(),
    )
