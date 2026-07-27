"""Liveness and readiness HTTP endpoints."""

from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from thytrader import __version__
from thytrader.api.dependencies import get_runtime_state

# FastAPI resolves this dependency annotation at runtime.
from thytrader.runtime import RuntimeState  # noqa: TC001

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """Stable process-health response exposed to operators."""

    service: Literal["api"]
    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    """Stable dependency-readiness response exposed to operators."""

    service: Literal["api"]
    status: Literal["ready", "not_ready"]
    version: str


@router.get("/live", response_model=HealthResponse)
async def get_liveness() -> HealthResponse:
    """Report that the API process can serve requests."""
    return HealthResponse(service="api", status="ok", version=__version__)


@router.get("/ready", response_model=ReadinessResponse)
async def get_readiness(
    response: Response,
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> ReadinessResponse:
    """Report that API startup completed successfully."""
    if not runtime.ready:
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
        return ReadinessResponse(service="api", status="not_ready", version=__version__)
    return ReadinessResponse(service="api", status="ready", version=__version__)
