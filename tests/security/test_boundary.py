"""Trust-boundary validation tests."""

from __future__ import annotations

from pydantic import SecretStr
import pytest

from thytrader.config import Settings
from thytrader.security.boundary import TrustBoundary, TrustBoundaryError


def test_rejects_hostile_host() -> None:
    """Host header validation must fail closed."""
    boundary = TrustBoundary(
        settings=Settings(
            installation_token=SecretStr("test-token"),
            trust_boundary_enabled=True,
            _env_file=None,
        ),
        credentials_dir=Settings().credentials_dir,
        enabled=True,
    )
    with pytest.raises(TrustBoundaryError, match="Host header is not allowed"):
        boundary.validate_request(
            method="POST",
            path="/api/v1/settings",
            host="evil.example",
            origin=None,
            authorization="Bearer test-token",
            csrf_token=None,
            csrf_cookie=None,
        )


def test_rejects_missing_installation_token_on_mutation() -> None:
    """Mutations require a Bearer installation credential."""
    boundary = TrustBoundary(
        settings=Settings(
            installation_token=SecretStr("test-token"),
            trust_boundary_enabled=True,
            _env_file=None,
        ),
        credentials_dir=Settings().credentials_dir,
        enabled=True,
    )
    with pytest.raises(TrustBoundaryError, match="Installation credential required"):
        boundary.validate_request(
            method="POST",
            path="/api/v1/deployments",
            host="127.0.0.1:8200",
            origin=None,
            authorization=None,
            csrf_token=None,
            csrf_cookie=None,
        )


def test_requires_csrf_for_browser_origin_mutations() -> None:
    """Browser-origin mutations require double-submit CSRF."""
    boundary = TrustBoundary(
        settings=Settings(
            installation_token=SecretStr("test-token"),
            trust_boundary_enabled=True,
            _env_file=None,
        ),
        credentials_dir=Settings().credentials_dir,
        enabled=True,
    )
    session = boundary.issue_session()
    with pytest.raises(TrustBoundaryError, match="CSRF token required"):
        boundary.validate_request(
            method="POST",
            path="/api/v1/settings",
            host="127.0.0.1:8200",
            origin="http://127.0.0.1:8200",
            authorization="Bearer test-token",
            csrf_token=None,
            csrf_cookie=None,
        )
    boundary.validate_request(
        method="POST",
        path="/api/v1/settings",
        host="127.0.0.1:8200",
        origin="http://127.0.0.1:8200",
        authorization="Bearer test-token",
        csrf_token=session.csrf_token,
        csrf_cookie=session.csrf_token,
    )
