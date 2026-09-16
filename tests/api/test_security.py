"""HTTP contracts for the application trust boundary."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from thytrader.api.app import create_app
from thytrader.config import Environment, Settings
from thytrader.security.models import CSRF_HEADER, INSTALLATION_AUTH_HEADER


def _auth_headers(token: str, *, csrf: str | None = None) -> dict[str, str]:
    headers = {INSTALLATION_AUTH_HEADER: f"Bearer {token}"}
    if csrf is not None:
        headers[CSRF_HEADER] = csrf
    return headers


def test_mutations_require_installation_token() -> None:
    """Unauthenticated deployment pause is rejected when trust boundary is enabled."""
    app = create_app(
        Settings(
            environment=Environment.TEST,
            installation_token=SecretStr("stage3-test-token"),
            trust_boundary_enabled=True,
            _env_file=None,
        )
    )
    client = TestClient(app)
    response = client.post("/api/v1/deployments/00000000-0000-0000-0000-000000000099/pause")
    assert response.status_code == 401


def test_security_session_advertises_published_policy_gate() -> None:
    """Session bootstrap reminds browsers that live needs a published policy."""
    app = create_app(
        Settings(
            environment=Environment.TEST,
            installation_token=SecretStr("stage3-test-token"),
            trust_boundary_enabled=True,
            _env_file=None,
        )
    )
    headers = _auth_headers("stage3-test-token")
    with TestClient(app) as client:
        response = client.get("/api/v1/security/session", headers=headers)
    assert response.status_code == 200
    assert response.json()["live_requires_published_policy"] is True
