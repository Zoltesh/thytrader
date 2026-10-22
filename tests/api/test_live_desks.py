"""Live agent desks over HTTP (ADR 0148): acknowledgement, policy reservation, live orders."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import TYPE_CHECKING, cast
from uuid import UUID

from tests.api.test_discretionary_orders import _client, _UsdReader
from tests.execution.test_discretionary import _LiveFillBroker
from thytrader.agent_desk.store import InMemoryDeskStore
from thytrader.risk.models import (
    CapitalAllocation,
    DeskAllocation,
    RiskPolicyDefinition,
    compiled_default_risk_policy,
)
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000b1")
_DESK = {
    "name": "live swing",
    "quote_currency": "USD",
    "starting_cash": "150",
    "products": ["BTC-USD"],
    "harness": "claude",
    "cadence_minutes": 240,
    "mode": "live",
}
_OPEN = {
    "action": "open",
    "thesis": "swing",
    "product_id": "BTC-USD",
    "side": "long",
    "size_fraction": "0.25",
    "entry": "market",
    "stop_price": "1",
    "take_profit_price": "100000000",
}


def _publish(risk: InMemoryRiskPolicyStore, *desks: DeskAllocation) -> None:
    """Publish a policy with a strategy allocation in force (and any desk reservations)."""
    policy = RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="json"),
            "allocations": [CapitalAllocation(strategy_id=_STRATEGY, allocated_quote="100")],
            "desk_allocations": list(desks),
        }
    )
    asyncio.run(risk.publish(policy))


def _live_client(risk: InMemoryRiskPolicyStore, broker: _LiveFillBroker) -> TestClient:
    """Live credentials, a fake live broker and venue reader, in-memory desks."""
    client = _client(
        execution=InMemoryExecutionStore(),
        live_broker=broker,
        live_credentials=True,
        quote_reader=_UsdReader(),
        risk=risk,
    )
    cast("FastAPI", client.app).state.desk_store = InMemoryDeskStore()
    return client


def _wake(client: TestClient, desk_id: str, answer: dict[str, str]) -> dict:
    """Brief, then post one answer."""
    brief = client.get(f"/api/v1/desks/{desk_id}/briefing").json()
    assert "LIVE trading desk" in brief["prompt"]
    posted = client.post(
        f"/api/v1/desks/{desk_id}/wakes",
        json={
            "raw_response": json.dumps(answer),
            "harness": "claude",
            "briefing_fingerprint": brief["fingerprint"],
        },
    )
    assert posted.status_code == 201, posted.text
    return posted.json()


def test_a_live_desk_needs_the_live_acknowledgement() -> None:
    """Without i_understand_live the create is a 428 and nothing is stored."""
    risk = InMemoryRiskPolicyStore()
    with _live_client(risk, _LiveFillBroker()) as client:
        refused = client.post("/api/v1/desks", json=_DESK)
        created = client.post("/api/v1/desks", json={**_DESK, "i_understand_live": True})
        listed = client.get("/api/v1/desks").json()
    assert refused.status_code == 428
    assert created.status_code == 201, created.text
    assert created.json()["mode"] == "live"
    assert created.json()["live_acknowledged_at"] is not None
    assert len(listed) == 1


def test_an_unreserved_live_desk_is_refused_and_a_reserved_one_trades_live() -> None:
    """While allocations are in force the desk needs its own reservation to place live."""
    risk = InMemoryRiskPolicyStore()
    broker = _LiveFillBroker()
    _publish(risk)
    with _live_client(risk, broker) as client:
        desk_id = client.post("/api/v1/desks", json={**_DESK, "i_understand_live": True}).json()[
            "desk_id"
        ]
        refused = _wake(client, desk_id, _OPEN)
        _publish(risk, DeskAllocation(desk_id=desk_id, allocated_quote="150"))
        applied = _wake(client, desk_id, _OPEN)
        short = _wake(client, desk_id, {**_OPEN, "side": "short"})
        detail = client.get(f"/api/v1/desks/{desk_id}").json()
        book = client.get(f"/api/v1/deployments/{applied['deployment_id']}").json()
    assert refused["outcome_code"] == "DISCRETIONARY_NOT_ALLOCATED", refused
    assert applied["outcome"] == "applied", applied
    assert book["mode"] == "live"
    assert broker.placed, "the live broker placed the entry"
    assert Decimal(detail["desk"]["cash"]) == Decimal("112.50")
    assert short["outcome_code"] == "DESK_SHORT_LIVE_UNSUPPORTED"
