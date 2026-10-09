"""``POST /api/v1/deployments`` with ``adopt_holdings`` (ADR 0124)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from tests.adoption_support import balance
from tests.execution.decision_support import strategy
from tests.execution.test_inventory_adoption_service import _Venue
from tests.strategy_fakes import SeededStrategyStore
from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance

_URL = "/api/v1/deployments"


class _Account:
    """A live account double holding 0.05 BTC and 100000 USD."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """The scripted balances."""
        return (balance("BTC", "0.05"), balance("USD", "100000"))

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


def _client(execution: InMemoryExecutionStore) -> tuple[TestClient, str]:
    """An app with the template strategy, live credentials and a published wide policy."""
    definition = strategy()
    strategies = SeededStrategyStore()
    fingerprint = strategy_fingerprint(definition)
    strategies.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    risk = InMemoryRiskPolicyStore()
    asyncio.run(
        risk.publish(
            compiled_default_risk_policy().model_copy(
                update={
                    "version": 2,
                    "max_portfolio_exposure_fraction": "1",
                    "per_product_max_exposure_fraction": "1",
                }
            )
        )
    )
    app = create_app(
        Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        ),
        strategy_store=strategies,
        execution_store=execution,
        paper_broker=PaperBroker(),
        live_broker=_Venue(),
        quote_reader=_Account(),
        risk_policy_store=risk,
        audit_event_store=InMemoryAuditEventStore(),
        market_data_service=MarketDataService(DemoMarketData()),
    )
    return TestClient(app), str(definition.strategy_id)


def _body(strategy_id: str, **overrides: object) -> dict[str, object]:
    """One live start that adopts 0.04 BTC."""
    payload: dict[str, object] = {
        "strategy_id": strategy_id,
        "mode": "live",
        "adopt_holdings": "0.04",
        "i_understand_live": True,
    }
    payload.update(overrides)
    return payload


def test_live_start_with_adoption_returns_an_open_bot() -> None:
    """201: the strategy bot starts OPEN with the adopted coins; nothing was bought."""
    execution = InMemoryExecutionStore()
    client, strategy_id = _client(execution)
    with client:
        response = client.post(_URL, json=_body(strategy_id))
    assert response.status_code == 201, response.text
    book = response.json()
    assert book["mode"] == "live" and book["kind"] == "strategy"
    assert book["status"] == "running" and book["phase"] == "open"
    [position] = book["positions"]
    assert Decimal(position["quantity"]) == Decimal("0.04") and position["side"] == "long"
    assert len(execution.deployments) == 1


@pytest.mark.parametrize(
    ("overrides", "status_code", "detail"),
    [
        (
            {"mode": "paper", "i_understand_live": False, "paper_starting_cash": "1000"},
            409,
            "ADOPTION_LIVE_ONLY",
        ),
        ({"adopt_holdings": "1"}, 409, "ADOPTION_QUANTITY_UNAVAILABLE"),
        ({"i_understand_live": False}, 428, "live_acknowledgement_required"),
        ({"adopt_holdings": "lots"}, 422, None),
    ],
)
def test_refusals_create_no_bot(
    overrides: dict[str, object], status_code: int, detail: str | None
) -> None:
    """Paper, too much, no acknowledgement and a bad quantity all leave nothing behind."""
    execution = InMemoryExecutionStore()
    client, strategy_id = _client(execution)
    with client:
        response = client.post(_URL, json=_body(strategy_id, **overrides))
    assert response.status_code == status_code, response.text
    if detail is not None:
        assert detail in str(response.json()["detail"])
    assert execution.deployments == {}
