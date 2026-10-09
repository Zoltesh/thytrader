"""HTTP contract of inventory adoption: preview, protect and sell (ADR 0124)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from tests.adoption_support import balance
from tests.execution.test_inventory_adoption_service import _Venue
from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import OrderKind

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance

_URL = "/api/v1/inventory-adoptions"


class _Account:
    """A live Coinbase account double holding 0.5 SOL and 100000 USD."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """The scripted balances."""
        return (balance("SOL", "0.5"), balance("USD", "100000"))

    async def get_permissions(self) -> tuple[str, ...]:
        """Unused."""
        return ()

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Unused."""
        del currency
        return None

    async def get_fee_profile(self) -> FeeProfile:
        """Unused."""
        raise AssertionError("get_fee_profile should not run")


def _client(execution: InMemoryExecutionStore, venue: _Venue) -> TestClient:
    """An app with live credentials, a published wide policy and scripted venue reads."""
    risk = InMemoryRiskPolicyStore()
    policy = compiled_default_risk_policy().model_copy(
        update={
            "version": 2,
            "max_portfolio_exposure_fraction": "1",
            "per_product_max_exposure_fraction": "1",
        }
    )
    asyncio.run(risk.publish(policy))
    settings = Settings(
        _env_file=None,
        coinbase_api_key_name=SecretStr("key"),
        coinbase_api_private_key=SecretStr("secret"),
    )
    app = create_app(
        settings,
        strategy_store=DisabledStrategyStore(),
        execution_store=execution,
        paper_broker=PaperBroker(),
        live_broker=venue,
        quote_reader=_Account(),
        risk_policy_store=risk,
        audit_event_store=InMemoryAuditEventStore(),
        market_data_service=MarketDataService(DemoMarketData()),
    )
    return TestClient(app)


def _body(**overrides: object) -> dict[str, object]:
    """One valid live protect request."""
    payload: dict[str, object] = {
        "mode": "live",
        "action": "protect",
        "product_id": "SOL-USD",
        "quantity": "0.3",
        "stop_price": "50000",
        "take_profit_price": "200000",
        "idempotency_key": "adopt-http-1",
        "origin": "human",
        "i_understand_live": True,
    }
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value is not None}


def test_protect_is_created_once_and_replayed_by_key() -> None:
    """201 with a protected discretionary book; the same key returns it without re-adopting."""
    execution = InMemoryExecutionStore()
    venue = _Venue()
    with _client(execution, venue) as client:
        first = client.post(_URL, json=_body())
        assert first.status_code == 201, first.text
        book = first.json()
        assert book["kind"] == "discretionary" and book["mode"] == "live"
        assert book["status"] == "running"
        again = client.post(_URL, json=_body())
        assert again.status_code == 201 and again.json()["id"] == book["id"]
    assert [kind for kind, _side, _quantity in venue.placed] == [OrderKind.TRIGGER_BRACKET]


def test_sell_creates_a_stopped_flatten_book_and_submits_nothing() -> None:
    """The response is the adopted book; the worker sells it."""
    execution = InMemoryExecutionStore()
    venue = _Venue()
    with _client(execution, venue) as client:
        response = client.post(
            _URL,
            json=_body(
                action="sell",
                quantity="all",
                stop_price=None,
                take_profit_price=None,
                idempotency_key="sell-http-1",
            ),
        )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "stopped"
    assert response.json()["lifecycle_command"] == "flatten"
    assert venue.placed == []


def test_live_without_acknowledgement_is_428() -> None:
    """The strict live acknowledgement is required; nothing is written."""
    execution = InMemoryExecutionStore()
    with _client(execution, _Venue()) as client:
        response = client.post(_URL, json=_body(i_understand_live=False))
    assert response.status_code == 428
    assert execution.intents == {}


def test_paper_is_409_live_only() -> None:
    """Paper has no venue holdings to adopt."""
    with _client(InMemoryExecutionStore(), _Venue()) as client:
        response = client.post(_URL, json=_body(mode="paper", i_understand_live=False))
    assert response.status_code == 409
    assert response.json()["detail"].startswith("ADOPTION_LIVE_ONLY")


def test_more_than_the_unmanaged_base_is_409() -> None:
    """The adoptable quantity bounds the request; nothing is written."""
    execution = InMemoryExecutionStore()
    with _client(execution, _Venue()) as client:
        response = client.post(_URL, json=_body(quantity="0.9"))
    assert response.status_code == 409
    assert response.json()["detail"].startswith("ADOPTION_QUANTITY_UNAVAILABLE")
    assert execution.deployments == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"stop_price": None},
        {"take_profit_price": None},
        {"action": "sell", "take_profit_price": None},
        {"quantity": "lots"},
        {"quantity": "-1"},
        {"action": "buy"},
        {"timeframe": "7m"},
        {"product_id": "doge"},
        {"origin": "runtime"},
        {"i_understand_live": "yes"},
    ],
)
def test_invalid_requests_are_422(overrides: dict[str, object]) -> None:
    """Shape errors never reach the service."""
    execution = InMemoryExecutionStore()
    with _client(execution, _Venue()) as client:
        response = client.post(_URL, json=_body(**overrides))
    assert response.status_code == 422, response.text
    assert execution.intents == {}


def test_preview_reports_balance_claims_adoptable_and_mark() -> None:
    """Read-only figures as exact decimal text; a bad product is 422."""
    with _client(InMemoryExecutionStore(), _Venue()) as client:
        response = client.get(f"{_URL}/preview", params={"product_id": "SOL-USD"})
        bad = client.get(f"{_URL}/preview", params={"product_id": "SOL"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["base_currency"] == "SOL" and payload["balance_total"] == "0.5"
    assert payload["adoptable"] == "0.5" and payload["claims"]["claimed"] == "0"
    assert payload["mark"] is not None and payload["protect_blocking_reasons"] == []
    assert bad.status_code == 422
