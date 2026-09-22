"""Application trust boundary: installation auth, Host/Origin, and CSRF."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import SecretStr  # noqa: TC002 - runtime credential comparison.

from thytrader.security.installation import resolve_installation_token
from thytrader.security.models import (
    SecuritySessionView,
    TrustBoundaryStatusView,
)

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.config import Settings

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "api", "testserver"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PUBLIC_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class TrustBoundaryError(RuntimeError):
    """Trust-boundary validation failed."""


@dataclass
class TrustBoundary:
    """Process-local trust boundary state."""

    enabled: bool
    installation_token: SecretStr
    credentials_dir: Path
    api_port: int
    containerized: bool
    _csrf_secrets: dict[str, str]

    def __init__(
        self,
        *,
        settings: Settings,
        credentials_dir: Path,
        enabled: bool = True,
    ) -> None:
        """Initialize installation auth and CSRF state."""
        self.enabled = enabled
        self.credentials_dir = credentials_dir
        self.api_port = settings.api_port
        self.containerized = settings.containerized
        self.installation_token = resolve_installation_token(
            configured=settings.installation_token,
            credentials_dir=credentials_dir,
        )
        self._csrf_secrets: dict[str, str] = {}

    @classmethod
    def from_settings(cls, settings: Settings, *, credentials_dir: Path) -> TrustBoundary:
        """Build one trust boundary from process settings."""
        return cls(
            settings=settings,
            credentials_dir=credentials_dir,
            enabled=settings.trust_boundary_enabled,
        )

    def status(self, *, credentials_env_file: Path) -> TrustBoundaryStatusView:
        """Return advertised trust-boundary configuration."""
        return TrustBoundaryStatusView(
            enabled=self.enabled,
            host_validation=True,
            origin_validation=True,
            csrf_required_for_browser=True,
            installation_token_configured=True,
            credentials_env_file=str(credentials_env_file),
            shared_credentials_volume=self.credentials_dir.is_dir(),
        )

    def issue_session(self) -> SecuritySessionView:
        """Mint a browser CSRF token bound to this process."""
        token = secrets.token_urlsafe(24)
        self._csrf_secrets[token] = token
        return SecuritySessionView(csrf_token=token)

    def validate_request(
        self,
        *,
        method: str,
        path: str,
        host: str | None,
        origin: str | None,
        authorization: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
    ) -> None:
        """Validate one HTTP request against the trust boundary."""
        if not self.enabled or self._is_public(path=path):
            return
        self._validate_host(host)
        if origin is not None:
            self._validate_origin(origin)
        upper_method = method.upper()
        if upper_method in _SAFE_METHODS and not self._requires_installation_on_read(path=path):
            return
        self._validate_installation_token(authorization)
        if origin is not None and upper_method not in _SAFE_METHODS:
            self._validate_csrf(csrf_token=csrf_token, csrf_cookie=csrf_cookie)

    def _is_public(self, *, path: str) -> bool:
        """True for health and OpenAPI metadata."""
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_PREFIXES)

    def _requires_installation_on_read(self, path: str) -> bool:
        """True when a GET still mints or exposes privileged session state."""
        return path == "/api/v1/security/session"

    def _validate_host(self, host: str | None) -> None:
        """Reject hostile Host headers."""
        if host is None or not host.strip():
            raise TrustBoundaryError("Host header is required.")
        hostname = host.split(":", 1)[0].strip().lower()
        if hostname in _LOOPBACK_HOSTS:
            return
        if self.containerized and hostname == "api":
            return
        raise TrustBoundaryError("Host header is not allowed.")

    def _validate_origin(self, origin: str) -> None:
        """Reject non-loopback browser origins regardless of port.

        Hostname-only comparison (ADR 0061 amendment 2026-09-22): the UI and
        the API listen on different loopback ports in every supported
        topology, so a port match would reject every legitimate browser
        mutation. Cross-site requests remain the CSRF gate's job.
        """
        parsed = urlparse(origin.strip())
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise TrustBoundaryError("Origin header is not allowed.")
        hostname = parsed.hostname.lower()
        if hostname not in _LOOPBACK_HOSTS:
            raise TrustBoundaryError("Origin header is not allowed.")

    def _validate_installation_token(self, authorization: str | None) -> None:
        """Require Bearer installation credential on mutations."""
        if authorization is None:
            raise TrustBoundaryError(
                "Installation credential required. Send Authorization: Bearer <token>."
            )
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise TrustBoundaryError("Installation credential must use Bearer scheme.")
        expected = self.installation_token.get_secret_value()
        if not secrets.compare_digest(token.strip(), expected):
            raise TrustBoundaryError("Installation credential is invalid.")

    def _validate_csrf(self, *, csrf_token: str | None, csrf_cookie: str | None) -> None:
        """Require double-submit CSRF for browser-origin mutations."""
        if not csrf_token or not csrf_cookie:
            raise TrustBoundaryError("CSRF token required for browser mutations.")
        if not secrets.compare_digest(csrf_token, csrf_cookie):
            raise TrustBoundaryError("CSRF token mismatch.")
        if csrf_token not in self._csrf_secrets:
            raise TrustBoundaryError("CSRF token is stale or unknown.")
