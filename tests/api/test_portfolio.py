"""Behavioral tests for the portfolio HTTP endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

    async def get_fee_profile(self) -> Any:
        """Raise a synthetic upstream failure."""
        raise RuntimeError("synthetic secret detail")

    async def list_open_orders(self) -> tuple[object, ...]:
        """Fail closed without returning a partial order listing."""
        raise RuntimeError("synthetic secret detail")


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
    assert payload["total_value"] == {"amount": "99792.17", "currency": "USD"}
    assert payload["total_value_basis"] == "usd_pegged_approximate"
    assert payload["totals"] == [
        {"amount": "98542.17", "currency": "USD"},
        {"amount": "1250.00", "currency": "USDC"},
    ]
    values = {asset["currency"]: asset["value"] for asset in payload["assets"]}
    assert values["USDC"] == {"amount": "1250.00", "currency": "USDC"}
    assert values["BTC"]["currency"] == "USD"
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


def test_operator_portfolio_never_reports_an_unknown_total_as_zero() -> None:
    """An unreadable account is a failed report with no total, not a 0 USDC total."""
    app = create_app(
        Settings(_env_file=None),
        portfolio_service=PortfolioService(FailingExchangeAccount()),
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/operator/portfolio").json()
    assert body["overall_status"] == "failed"
    assert body["payload"]["total_value"] is None
    assert body["payload"]["total_value_basis"] is None
    assert body["payload"]["totals"] == []


def test_operator_portfolio_reports_per_currency_totals() -> None:
    """The operator report carries the same exact per-currency totals as the API."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        body = client.get("/api/v1/operator/portfolio").json()
    assert body["payload"]["total_value_basis"] == "usd_pegged_approximate"
    assert body["payload"]["totals"] == [
        {"amount": "98542.17", "currency": "USD"},
        {"amount": "1250.00", "currency": "USDC"},
    ]
