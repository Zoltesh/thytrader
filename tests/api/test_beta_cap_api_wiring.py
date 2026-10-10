"""BTC-beta cap evidence reaches the HTTP entry paths (ADR 0125).

Discretionary place-order, inventory-adoption protect and live start with
``adopt_holdings`` each pass the app's market data to the β loader. A path that dropped it
would leave the evidence unloaded and refuse every entry once the cap is set.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from tests.adoption_support import balance
from tests.beta_support import DailyBetaProvider, DemoWithDailyBeta, beta_policy
from tests.execution.decision_support import strategy
from tests.execution.test_inventory_adoption_service import _Venue
from tests.strategy_fakes import SeededStrategyStore
from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.execution.paper import PaperBroker
from thytrader.risk.beta_evidence import DEFAULT_BETA_CACHE
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from decimal import Decimal

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.library import StrategyStore


@pytest.fixture(autouse=True)
def _fresh_beta_cache() -> Iterator[None]:
    """The API uses the process-wide β cache; isolate it per test."""
    DEFAULT_BETA_CACHE.clear()
    yield
    DEFAULT_BETA_CACHE.clear()


class _Account:
    """A live account double holding 0.5 SOL, 0.05 ETH and 100000 USD."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """The scripted balances."""
        return (balance("SOL", "0.5"), balance("ETH", "0.05"), balance("USD", "100000"))

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


def _client(
    provider: DemoWithDailyBeta,
    policy: RiskPolicyDefinition,
    *,
    strategies: StrategyStore | None = None,
) -> TestClient:
    """An app with live credentials, ``policy`` published and the β-aware market data."""
    risk = InMemoryRiskPolicyStore()
    asyncio.run(risk.publish(policy))
    app = create_app(
        Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        ),
        strategy_store=strategies if strategies is not None else DisabledStrategyStore(),
        execution_store=InMemoryExecutionStore(),
        paper_broker=PaperBroker(),
        live_broker=_Venue(),
        quote_reader=_Account(),
        risk_policy_store=risk,
        audit_event_store=InMemoryAuditEventStore(),
        market_data_service=provider.service(),
    )
    return TestClient(app)


def _provider(*, failing: frozenset[str] = frozenset()) -> DemoWithDailyBeta:
    """Demo candles plus daily β history for ETH-USD (1.2) and SOL-USD (1.5)."""
    return DemoWithDailyBeta(
        DailyBetaProvider(betas={"ETH-USD": 1.2, "SOL-USD": 1.5}, failing=set(failing))
    )


_DISCRETIONARY = {
    "mode": "paper",
    "product_id": "ETH-USD",
    "entry_kind": "marketable",
    "stop_price": "50000",
    "take_profit_price": "200000",
    "origin": "human",
    "idempotency_key": "beta-http-1",
    "timeframe": "5m",
    "quantity": "0.001",
    "paper_starting_cash": "10000",
}


@pytest.mark.parametrize(
    ("policy", "beta_reads"),
    [(beta_policy(), True), (beta_policy(fraction=None), False)],
)
def test_discretionary_order_passes_market_data_to_the_beta_cap(
    policy: RiskPolicyDefinition, *, beta_reads: bool
) -> None:
    """A set cap reads ETH history and admits the ticket; unset reads nothing."""
    provider = _provider()
    with _client(provider, policy) as client:
        response = client.post("/api/v1/discretionary-orders", json=_DISCRETIONARY)
    assert response.status_code == 201, response.text
    assert ("ETH-USD" in provider.daily.requested_products()) is beta_reads
    assert bool(provider.daily.requests) is beta_reads


def test_discretionary_order_without_beta_history_is_refused() -> None:
    """Unreadable ETH history refuses the ticket with the β cause; nothing rests."""
    provider = _provider(failing=frozenset({"ETH-USD"}))
    with _client(provider, beta_policy()) as client:
        response = client.post("/api/v1/discretionary-orders", json=_DISCRETIONARY)
    assert response.status_code == 409
    assert "BTC beta unavailable for ETH-USD vs BTC-USD: fetch_failed" in response.text


_PROTECT = {
    "mode": "live",
    "action": "protect",
    "product_id": "SOL-USD",
    "quantity": "0.3",
    "stop_price": "50000",
    "take_profit_price": "200000",
    "idempotency_key": "beta-adopt-1",
    "origin": "human",
    "i_understand_live": True,
}


def test_inventory_adoption_protect_is_beta_capped_with_market_data() -> None:
    """Protect adopts SOL in kind after reading its β; an outage refuses it."""
    provider = _provider()
    with _client(provider, beta_policy()) as client:
        response = client.post("/api/v1/inventory-adoptions", json=_PROTECT)
    assert response.status_code == 201, response.text
    assert "SOL-USD" in provider.daily.requested_products()

    DEFAULT_BETA_CACHE.clear()  # Otherwise the outage falls back to the good series.
    failing = _provider(failing=frozenset({"SOL-USD"}))
    with _client(failing, beta_policy()) as client:
        refused = client.post("/api/v1/inventory-adoptions", json=_PROTECT)
    assert refused.status_code == 409
    assert "BTC_BETA_UNAVAILABLE" in refused.text


def test_inventory_adoption_sell_reads_no_beta() -> None:
    """Selling held coins reduces risk: no β read, even with the cap set."""
    provider = _provider()
    sell = {
        **{key: value for key, value in _PROTECT.items() if "price" not in key},
        "action": "sell",
        "quantity": "all",
        "idempotency_key": "beta-sell-1",
    }
    with _client(provider, beta_policy()) as client:
        response = client.post("/api/v1/inventory-adoptions", json=sell)
    assert response.status_code == 201, response.text
    assert provider.daily.requests == []


def _eth_strategy() -> StrategyDefinition:
    """The template strategy on ETH-USD."""
    payload = strategy().model_dump(mode="python", by_alias=True)
    payload["instrument"] = {
        "product_id": "ETH-USD",
        "base_currency": "ETH",
        "quote_currency": "USD",
    }
    return StrategyDefinition.model_validate(payload)


def _seeded(definition: StrategyDefinition) -> SeededStrategyStore:
    """A strategy store with ``definition`` published."""
    strategies = SeededStrategyStore()
    fingerprint = strategy_fingerprint(definition)
    strategies.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    return strategies


def test_live_start_adopting_holdings_is_beta_capped_with_market_data() -> None:
    """``adopt_holdings`` reads ETH's β; an outage refuses the start and creates no bot."""
    definition = _eth_strategy()
    body = {
        "strategy_id": str(definition.strategy_id),
        "mode": "live",
        "adopt_holdings": "0.04",
        "i_understand_live": True,
    }
    provider = _provider()
    with _client(provider, beta_policy(), strategies=_seeded(definition)) as client:
        response = client.post("/api/v1/deployments", json=body)
    assert response.status_code == 201, response.text
    assert "ETH-USD" in provider.daily.requested_products()

    DEFAULT_BETA_CACHE.clear()  # Otherwise the outage falls back to the good series.
    failing = _provider(failing=frozenset({"ETH-USD"}))
    with _client(failing, beta_policy(), strategies=_seeded(definition)) as client:
        refused = client.post("/api/v1/deployments", json=body)
    assert refused.status_code == 409
    assert "BTC_BETA_UNAVAILABLE" in refused.text
