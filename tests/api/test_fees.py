"""Unit tests for the fees API endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService


def test_fees_endpoint_demo_service_returns_fee_profile() -> None:
    """GET /api/v1/fees returns the validated demo fee profile."""
    service = PortfolioService(DemoExchangeAccount(), demo=True)
    app = create_app(Settings(_env_file=None), portfolio_service=service)

    with TestClient(app) as client:
        response = client.get("/api/v1/fees")

    assert response.status_code == 200
    data = response.json()
    assert data["fee_tier"] == "Tier 1"
    assert data["taker_fee_rate"] == "0.0060"
    assert data["maker_fee_rate"] == "0.0040"
    assert data["usd_volume_30d"] == "15250.00"
    assert data["source"] == "coinbase"


def test_fees_endpoint_service_failure_returns_redacted_502() -> None:
    """Service errors are caught and returned as static 502."""

    class FailingExchange:
        async def list_balances(self) -> Any:
            return ()

        async def get_permissions(self) -> Any:
            return ()

        async def get_usd_price(self, currency: str) -> Any:
            del currency
            return None

        async def get_fee_profile(self) -> Any:
            raise RuntimeError("Underlying Coinbase network timeout")

    service = PortfolioService(FailingExchange())  # type: ignore[arg-type]
    app = create_app(Settings(_env_file=None), portfolio_service=service)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fees")

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "fees_unavailable",
            "message": "Fee profile is temporarily unavailable.",
        }
    }


def test_fees_endpoint_rejects_forged_profile() -> None:
    """Forged / schema-violating return value from service fails closed 502."""

    class ForgedExchange:
        async def list_balances(self) -> Any:
            return ()

        async def get_permissions(self) -> Any:
            return ()

        async def get_usd_price(self, currency: str) -> Any:
            del currency
            return None

        async def get_fee_profile(self) -> Any:
            return {"invalid": "payload"}

    service = PortfolioService(ForgedExchange())  # type: ignore[arg-type]
    app = create_app(Settings(_env_file=None), portfolio_service=service)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fees")

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "fees_unavailable",
            "message": "Fee profile is temporarily unavailable.",
        }
    }


def test_fees_endpoint_rejects_forged_naive_as_of() -> None:
    """A fee profile with a naive as_of must fail closed 502, not serialize."""

    class NaiveDateTimeProfile:
        async def list_balances(self) -> Any:
            return ()

        async def get_permissions(self) -> Any:
            return ()

        async def get_usd_price(self, currency: str) -> Any:
            del currency
            return None

        async def get_fee_profile(self) -> Any:
            return {
                "taker_fee_rate": Decimal("0.012"),
                "maker_fee_rate": Decimal("0.006"),
                "usd_volume_30d": Decimal("0"),
                "fee_tier": "Intro 1",
                "as_of": datetime(2026, 8, 19, 12),  # noqa: DTZ001 - intentionally naive hostile payload
                "source": "coinbase",
            }

    service = PortfolioService(NaiveDateTimeProfile())  # type: ignore[arg-type]
    app = create_app(Settings(_env_file=None), portfolio_service=service)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fees")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "fees_unavailable"


def test_fees_endpoint_rejects_forged_out_of_range_rates() -> None:
    """Negative or >1 fee rates must fail closed 502, not serialize."""

    class OutOfRangeProfile:
        async def list_balances(self) -> Any:
            return ()

        async def get_permissions(self) -> Any:
            return ()

        async def get_usd_price(self, currency: str) -> Any:
            del currency
            return None

        async def get_fee_profile(self) -> Any:
            return {
                "taker_fee_rate": Decimal("-0.05"),
                "maker_fee_rate": Decimal("2.5"),
                "usd_volume_30d": Decimal("-100"),
                "fee_tier": "Intro 1",
                "as_of": datetime(2026, 8, 19, 12, tzinfo=UTC),
                "source": "coinbase",
            }

    service = PortfolioService(OutOfRangeProfile())  # type: ignore[arg-type]
    app = create_app(Settings(_env_file=None), portfolio_service=service)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fees")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "fees_unavailable"


def test_fees_endpoint_rejects_forged_zero_offset_non_utc_datetime() -> None:
    """A named zero-offset timezone must not be mistaken for UTC."""

    class ForgedZoneProfile:
        async def list_balances(self) -> Any:
            return ()

        async def get_permissions(self) -> Any:
            return ()

        async def get_usd_price(self, currency: str) -> Any:
            del currency
            return None

        async def get_fee_profile(self) -> Any:
            return SimpleNamespace(
                taker_fee_rate=Decimal("0.012"),
                maker_fee_rate=Decimal("0.006"),
                usd_volume_30d=Decimal("0"),
                fee_tier="Intro 1",
                as_of=datetime(
                    2026, 8, 19, 12, tzinfo=timezone(timedelta(0), "forged-zero-offset-zone")
                ),
                source="coinbase",
            )

    service = PortfolioService(ForgedZoneProfile())  # type: ignore[arg-type]
    app = create_app(Settings(_env_file=None), portfolio_service=service)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fees")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "fees_unavailable"
