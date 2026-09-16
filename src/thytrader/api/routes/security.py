"""HTTP contract for trust-boundary session bootstrap and status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from thytrader.api.dependencies import get_trust_boundary
from thytrader.security.boundary import TrustBoundary  # noqa: TC001 - FastAPI Depends.
from thytrader.security.models import SecuritySessionView, TrustBoundaryStatusView

router = APIRouter(prefix="/api/v1/security", tags=["security"])


@router.get("/status", response_model=TrustBoundaryStatusView)
async def get_security_status(
    request: Request,
    boundary: Annotated[TrustBoundary, Depends(get_trust_boundary)],
) -> TrustBoundaryStatusView:
    """Return trust-boundary configuration without secrets."""
    env_path = getattr(request.app.state, "credentials_env_file", None)
    credentials_env_file = env_path if env_path is not None else boundary.credentials_dir / ".env"
    return boundary.status(credentials_env_file=credentials_env_file)


@router.get("/session", response_model=SecuritySessionView)
async def get_security_session(
    request: Request,
    boundary: Annotated[TrustBoundary, Depends(get_trust_boundary)],
) -> SecuritySessionView:
    """Return a CSRF token after installation auth (middleware-enforced)."""
    session = boundary.issue_session()
    request.app.state.last_security_session = session.csrf_token
    return session
