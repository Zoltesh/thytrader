"""FastAPI middleware enforcing the application trust boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request  # noqa: TC002 - middleware receives live requests.
from starlette.responses import JSONResponse, Response

from thytrader.security.boundary import TrustBoundaryError
from thytrader.security.models import CSRF_COOKIE, CSRF_HEADER, INSTALLATION_AUTH_HEADER

if TYPE_CHECKING:
    from thytrader.security.boundary import TrustBoundary


class TrustBoundaryMiddleware(BaseHTTPMiddleware):
    """Validate Host, Origin, installation auth, and CSRF on each request."""

    def __init__(self, app: Starlette, *, boundary: TrustBoundary) -> None:
        """Bind one trust boundary instance."""
        super().__init__(app)
        self._boundary = boundary

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Reject untrusted requests before route handlers run."""
        try:
            self._boundary.validate_request(
                method=request.method,
                path=request.url.path,
                host=request.headers.get("host"),
                origin=request.headers.get("origin"),
                authorization=request.headers.get(INSTALLATION_AUTH_HEADER.lower()),
                csrf_token=request.headers.get(CSRF_HEADER.lower()),
                csrf_cookie=request.cookies.get(CSRF_COOKIE),
            )
        except TrustBoundaryError as error:
            return JSONResponse(status_code=401, content={"detail": str(error)})
        response = await call_next(request)
        if request.url.path == "/api/v1/security/session" and request.method.upper() == "GET":
            session = getattr(request.app.state, "last_security_session", None)
            if session is not None:
                response.set_cookie(
                    CSRF_COOKIE,
                    session,
                    httponly=False,
                    samesite="strict",
                    secure=False,
                    path="/",
                )
        return response
