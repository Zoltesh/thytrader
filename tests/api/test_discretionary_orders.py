"""HTTP contracts for on-demand discretionary orders."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.execution.test_discretionary import _LiveFillBroker, _TimeoutBroker
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.exchanges.models import ExchangeBalance
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.risk.models import CapitalAllocation, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.authoring import DisabledStrategyDraftStore
from thytrader.strategies.publication import DisabledStrategyPublicationStore

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.protocols import ExchangeAccount
    from thytrader.execution.broker import Broker


class _UsdReader:
    """Return a positive USD remaining quote for live sizing tests."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """One USD balance."""
        return (
            ExchangeBalance(
                currency="USD",
                name="USD",
                available=Decimal("20000"),
                hold=Decimal("0"),
            ),
        )

    async def get_permissions(self) -> tuple[str, ...]:
        """Unused in discretionary live tests."""
        return ()

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Unused in discretionary live tests."""
        del currency
        return None

    async def get_fee_profile(self) -> FeeProfile:
        """Fee profile is unused when injecting a quote reader."""
        raise AssertionError("get_fee_profile should not run")


def _client(
    *,
    execution: InMemoryExecutionStore,
    paper_broker: Broker | None = None,
    live_broker: Broker | None = None,
    live_credentials: bool = False,
    risk: InMemoryRiskPolicyStore | None = None,
    quote_reader: ExchangeAccount | None = None,
) -> TestClient:
    """Build an API client that never constructs a real Coinbase REST client in tests."""
    settings = Settings(_env_file=None)
    if live_credentials:
        settings = Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        )
    app = create_app(
        settings,
        strategy_store=DisabledStrategyPublicationStore(),
        strategy_draft_store=DisabledStrategyDraftStore(),
        execution_store=execution,
        paper_broker=paper_broker if paper_broker is not None else PaperBroker(),
        live_broker=live_broker,
        quote_reader=quote_reader,
        risk_policy_store=risk,
        audit_event_store=InMemoryAuditEventStore(),
        market_data_service=MarketDataService(DemoMarketData()),
    )
    return TestClient(app)


def _body(**overrides: str) -> dict[str, str]:
    """Return one valid paper marketable ticket."""
    payload = {
        "mode": "paper",
        "product_id": "BTC-USD",
        "entry_kind": "marketable",
        "stop_price": "50000",
        "take_profit_price": "200000",
        "origin": "human",
        "idempotency_key": "http-1",
        "timeframe": "5m",
        "quantity": "0.01",
        "paper_starting_cash": "10000",
    }
    payload.update(overrides)
    return payload


def test_paper_post_persists_intent_and_is_idempotent() -> None:
    """POST paper places once; the same idempotency key does not submit again."""
    execution = InMemoryExecutionStore()
    timeout = _TimeoutBroker()
    with _client(execution=execution, paper_broker=timeout) as client:
        first = client.post("/api/v1/discretionary-orders", json=_body())
        assert first.status_code == 201
        assert first.json()["kind"] == "discretionary"
        assert first.json()["strategy_fingerprint"] is None
        assert first.json()["maker_fee_rate"] == "0.001"
        assert first.json()["taker_fee_rate"] == "0.002"
        assert timeout.place_calls == 1
        second = client.post("/api/v1/discretionary-orders", json=_body())
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]
        assert timeout.place_calls == 1


def test_risk_denial_does_not_persist_intent() -> None:
    """Allocations deny discretionary orders before intent persist."""
    execution = InMemoryExecutionStore()
    risk = InMemoryRiskPolicyStore()

    async def _publish() -> None:
        await risk.publish(
            compiled_default_risk_policy().model_copy(
                update={
                    "allocations": (
                        CapitalAllocation(strategy_id=uuid4(), allocated_quote="10000"),
                    )
                }
            )
        )

    asyncio.run(_publish())
    with _client(execution=execution, risk=risk) as client:
        response = client.post("/api/v1/discretionary-orders", json=_body())
    assert response.status_code == 409
    assert execution.intents == {}


def test_live_without_credentials_is_conflict() -> None:
    """Live tickets require configured credentials."""
    execution = InMemoryExecutionStore()
    with _client(execution=execution) as client:
        payload = _body(mode="live")
        payload.pop("paper_starting_cash")
        response = client.post("/api/v1/discretionary-orders", json=payload)
    assert response.status_code == 409


def test_live_fake_broker_attaches_bracket_without_coinbase() -> None:
    """Injected live fakes attach SL/TP on the entry instead of a second OCO."""
    execution = InMemoryExecutionStore()
    broker = _LiveFillBroker()
    with _client(
        execution=execution,
        live_broker=broker,
        live_credentials=True,
        quote_reader=_UsdReader(),
    ) as client:
        response = client.post(
            "/api/v1/discretionary-orders",
            json={
                "mode": "live",
                "product_id": "BTC-USD",
                "entry_kind": "marketable",
                "stop_price": "50000",
                "take_profit_price": "200000",
                "origin": "agent",
                "idempotency_key": "live-http",
                "timeframe": "5m",
                "quantity": "0.01",
            },
        )
    assert response.status_code == 201
    payload = response.json()
    assert all(order["kind"] != "trigger_bracket" for order in payload["orders"])
    assert payload["orders"][0]["take_profit_price"] is not None
    assert payload["orders"][0]["stop_trigger_price"] is not None


def test_paper_short_post_opens_a_short_position() -> None:
    """HTTP side=short sells to open and records position.side."""
    execution = InMemoryExecutionStore()
    with _client(execution=execution) as client:
        response = client.post(
            "/api/v1/discretionary-orders",
            json=_body(
                side="short",
                stop_price="200000",
                take_profit_price="50000",
                idempotency_key="http-short",
            ),
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["position"]["side"] == "short"
    assert any(order["side"] == "sell" for order in payload["orders"])


def test_live_short_without_base_is_conflict() -> None:
    """USD-only live inventory cannot open a spot short."""
    execution = InMemoryExecutionStore()
    with _client(
        execution=execution,
        live_broker=_LiveFillBroker(),
        live_credentials=True,
        quote_reader=_UsdReader(),
    ) as client:
        response = client.post(
            "/api/v1/discretionary-orders",
            json={
                "mode": "live",
                "product_id": "BTC-USD",
                "side": "short",
                "entry_kind": "marketable",
                "stop_price": "200000",
                "take_profit_price": "50000",
                "origin": "agent",
                "idempotency_key": "live-short-no-base",
                "timeframe": "5m",
                "quantity": "0.01",
            },
        )
    assert response.status_code == 409
    assert "INSUFFICIENT_BASE_FOR_SPOT_SHORT" in response.json()["detail"]
    assert not execution.intents


def test_paper_discretionary_persists_fee_rates_and_rejects_live_fees() -> None:
    """Paper tickets store fee assumptions; live tickets reject them."""
    execution = InMemoryExecutionStore()
    with _client(execution=execution) as client:
        created = client.post(
            "/api/v1/discretionary-orders",
            json=_body(maker_fee_rate="0.0025", taker_fee_rate="0.004"),
        )
        one_sided = client.post(
            "/api/v1/discretionary-orders",
            json=_body(idempotency_key="http-2", maker_fee_rate="0.0025"),
        )
    assert created.status_code == 201
    assert created.json()["maker_fee_rate"] == "0.0025"
    assert created.json()["taker_fee_rate"] == "0.004"
    assert one_sided.status_code == 409

    live_execution = InMemoryExecutionStore()
    with _client(execution=live_execution, live_credentials=True, quote_reader=_UsdReader()) as client:
        payload = _body(mode="live", maker_fee_rate="0.001", taker_fee_rate="0.002")
        payload.pop("paper_starting_cash")
        response = client.post("/api/v1/discretionary-orders", json=payload)
    assert response.status_code == 409
