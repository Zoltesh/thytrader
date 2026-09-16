"""Write-only Coinbase credentials HTTP contract."""

from pathlib import Path

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.credentials.models import INVALID_CREDENTIALS_PAYLOAD
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService

_SYNTHETIC_KEY_NAME = "organizations/example/apiKeys/thytrader-test"
_SYNTHETIC_PRIVATE_KEY = (
    "-----BEGIN EC PRIVATE KEY-----\nSYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO\n-----END EC PRIVATE KEY-----"
)


def _app(tmp_path: Path) -> TestClient:
    """Build an API app that persists credentials into ``tmp_path``."""
    env_file = tmp_path / ".env"
    app = create_app(
        Settings(_env_file=None),
        audit_event_store=InMemoryAuditEventStore(),
        credentials_env_file=env_file,
        portfolio_service=PortfolioService(DemoExchangeAccount(), demo=True),
    )
    return TestClient(app)


def test_get_reports_unconfigured_without_secret_fields(tmp_path: Path) -> None:
    """GET is presence-only and never includes key names or private keys."""
    with _app(tmp_path) as client:
        response = client.get("/api/v1/credentials/coinbase")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "coinbase"
    assert payload["configured"] is False
    assert "api_key_name" not in payload
    assert "private_key" not in payload
    assert "THYTRADER_COINBASE" not in response.text


def test_put_hot_reloads_and_does_not_echo_secrets(tmp_path: Path) -> None:
    """PUT stores secrets server-side, rebuilds clients, and omits them from JSON."""
    with _app(tmp_path) as client:
        written = client.put(
            "/api/v1/credentials/coinbase",
            json={
                "api_key_name": _SYNTHETIC_KEY_NAME,
                "private_key": _SYNTHETIC_PRIVATE_KEY,
            },
        )
        assert written.status_code == 200
        body = written.json()
        assert body["configured"] is True
        assert body["api_hot_reloaded"] is True
        assert body["workers_require_restart"] is True
        assert body["persisted"] is True
        assert _SYNTHETIC_KEY_NAME not in written.text
        assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in written.text
        fetched = client.get("/api/v1/credentials/coinbase")
        assert fetched.json()["configured"] is True
        assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in fetched.text
        configuration = client.get("/api/v1/operator/configuration")
        assert configuration.json()["payload"]["coinbase_credentials_configured"] is True
        assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in configuration.text
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "THYTRADER_COINBASE_API_KEY_NAME=" in env_text
        events = client.get("/api/v1/audit-events")
        actions = [item["action"] for item in events.json()["events"]]
        assert "set_coinbase_credentials" in actions
        for event in events.json()["events"]:
            assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in event["detail"]


def test_put_422_does_not_echo_private_key(tmp_path: Path) -> None:
    """Invalid PUT bodies use a generic 422 and never include the submitted key."""
    with _app(tmp_path) as client:
        response = client.put(
            "/api/v1/credentials/coinbase",
            json={"api_key_name": "", "private_key": _SYNTHETIC_PRIVATE_KEY},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == INVALID_CREDENTIALS_PAYLOAD
    assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in response.text
    assert "input" not in response.text


def test_delete_clears_credentials_and_returns_to_unconfigured(tmp_path: Path) -> None:
    """DELETE clears process secrets and writes empty dotenv placeholders."""
    with _app(tmp_path) as client:
        client.put(
            "/api/v1/credentials/coinbase",
            json={
                "api_key_name": _SYNTHETIC_KEY_NAME,
                "private_key": _SYNTHETIC_PRIVATE_KEY,
            },
        )
        cleared = client.delete("/api/v1/credentials/coinbase")
        assert cleared.status_code == 200
        assert cleared.json()["configured"] is False
        assert cleared.json()["api_hot_reloaded"] is True
        assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in cleared.text
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "SYNTHETIC-COINBASE-PRIVATE-KEY-DO-NOT-ECHO" not in env_text
        events = client.get("/api/v1/audit-events")
        actions = [item["action"] for item in events.json()["events"]]
        assert "clear_coinbase_credentials" in actions
