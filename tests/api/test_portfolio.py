"""Behavioral tests for the portfolio HTTP endpoint."""

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.portfolio.service import PortfolioService

if TYPE_CHECKING:
    from thytrader.exchanges.models import ExchangeBalance


class FailingExchangeAccount:
    """Exchange boundary that fails without exposing sensitive details."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Raise a synthetic upstream failure."""
        raise RuntimeError("synthetic secret detail")

    async def get_permissions(self) -> tuple[str, ...]:
        """Return no permissions because balance loading fails first."""
        return ()

    async def get_usd_price(self, currency: str) -> None:
        """Return no price for the unreachable exchange."""
        del currency


def test_portfolio_endpoint_returns_demo_data_without_credentials() -> None:
    """A clean install should expose a practical demo portfolio immediately."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio")

    assert response.status_code == 200
    payload = response.json()
    assert payload["demo"] is True
    assert payload["connection"] == {
        "provider": "coinbase",
        "status": "demo",
        "permissions": ["view", "trade"],
    }
    assert payload["total_value"]["currency"] == "USD"
    assert isinstance(payload["total_value"]["amount"], str)
    assert {asset["currency"] for asset in payload["assets"]} == {"BTC", "ETH", "USDC"}


def test_portfolio_endpoint_does_not_expose_configured_credentials() -> None:
    """Portfolio payloads must never contain Coinbase credential material."""
    key_name = "organizations/example/apiKeys/example"
    private_key = "synthetic-private-key"
    settings = Settings(
        coinbase_api_key_name=key_name,
        coinbase_api_private_key=private_key,
        _env_file=None,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio")

    rendered = response.text
    assert key_name not in rendered
    assert private_key not in rendered


def test_portfolio_failure_is_redacted_and_matches_openapi_schema() -> None:
    """Failure payload and documented schema should share the same detail envelope."""
    app = create_app(
        Settings(_env_file=None),
        portfolio_service=PortfolioService(FailingExchangeAccount()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio")
        openapi = client.get("/openapi.json").json()

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "coinbase_unavailable",
            "message": "Coinbase is temporarily unavailable. Check your credentials and try again.",
        }
    }
    assert "synthetic secret detail" not in response.text
    schema = openapi["paths"]["/api/v1/portfolio"]["get"]["responses"]["502"]["content"]
    assert schema["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
