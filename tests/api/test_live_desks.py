"""Live agent desks over HTTP (ADR 0148): acknowledgement, policy reservation, live orders."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

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
    "entry": "limit",
    "limit_price": "50000",
    "stop_price": "1",
    "take_profit_price": "100000000",
}


def _publish(risk: InMemoryRiskPolicyStore, *desks: DeskAllocation) -> None:
    """Publish a policy with a strategy allocation in force (and any desk reservations)."""
    policy = RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="json"),
            "quote_currency": "USD",
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
            "config_fingerprint": brief["config_fingerprint"],
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
        market_rows = client.get(f"/api/v1/desks/{desk_id}/briefing").json()["market"]
        close = Decimal(market_rows[0]["last_close"])
        order = {**_OPEN, "limit_price": str((close * Decimal("0.999")).quantize(Decimal("0.01")))}
        refused = _wake(client, desk_id, order)
        _publish(risk, DeskAllocation(desk_id=desk_id, allocated_quote="150"))
        applied = _wake(client, desk_id, order)
        short = _wake(client, desk_id, {**order, "side": "short"})
        market = _wake(client, desk_id, {**order, "entry": "market"})
        detail = client.get(f"/api/v1/desks/{desk_id}").json()
        book = client.get(
            f"/api/v1/deployments/{applied['deployment_id']}", params={"detail": "full"}
        ).json()
    assert refused["outcome_code"] == "DISCRETIONARY_NOT_ALLOCATED", refused
    assert applied["outcome"] == "applied", applied
    assert book["mode"] == "live"
    assert broker.placed, "the live broker placed the entry"
    assert Decimal(detail["desk"]["cash"]) == Decimal("112.50")
    assert short["outcome_code"] == "DESK_SHORT_LIVE_UNSUPPORTED"
    assert market["outcome_code"] == "DESK_LIVE_LIMIT_ONLY"
    entry = book["orders"][0]
    assert entry["kind"] == "post_only_limit"
    assert Decimal(entry["quantity"]) * Decimal(entry["price"]) <= Decimal("37.50")


def test_the_reservation_must_hold_the_whole_allocation_not_just_the_notional() -> None:
    """A reservation between the order's notional and its allocation (fee headroom) is refused."""
    risk = InMemoryRiskPolicyStore()
    with _live_client(risk, _LiveFillBroker()) as client:
        desk_id = client.post("/api/v1/desks", json={**_DESK, "i_understand_live": True}).json()[
            "desk_id"
        ]
        _publish(risk, DeskAllocation(desk_id=desk_id, allocated_quote="37"))
        market_rows = client.get(f"/api/v1/desks/{desk_id}/briefing").json()["market"]
        close = Decimal(market_rows[0]["last_close"])
        order = {**_OPEN, "limit_price": str((close * Decimal("0.999")).quantize(Decimal("0.01")))}
        refused = _wake(client, desk_id, order)
    # 25% of 150 is a 37.50 allocation: its 36.75 notional fits 37, the allocation does not.
    assert refused["outcome_code"] == "ALLOCATION_EXCEEDED", refused


@pytest.mark.parametrize(
    "change",
    [
        {"model": "other"},
        {"harness": "codex"},
        {"mandate": "trade more"},
        {"caps": {"max_position_fraction": "0.5"}},
    ],
)
def test_changing_how_a_live_desk_trades_needs_the_live_acknowledgement(
    change: dict[str, object],
) -> None:
    """Harness, model, mandate or caps change only with i_understand_live (428 otherwise)."""
    risk = InMemoryRiskPolicyStore()
    with _live_client(risk, _LiveFillBroker()) as client:
        desk_id = client.post("/api/v1/desks", json={**_DESK, "i_understand_live": True}).json()[
            "desk_id"
        ]
        refused = client.patch(f"/api/v1/desks/{desk_id}", json=change)
        renamed = client.patch(f"/api/v1/desks/{desk_id}", json={"name": "renamed"})
        changed = client.patch(
            f"/api/v1/desks/{desk_id}", json={**change, "i_understand_live": True}
        )
    assert refused.status_code == 428
    assert renamed.status_code == 200, renamed.text
    assert changed.status_code == 200, changed.text
    assert [entry["revision"] for entry in changed.json()["changes"]] == [2, 3]


def test_a_live_desk_refuses_a_no_trade_or_open_from_another_model_over_http() -> None:
    """The API, not only the runner, binds a live desk's answers to its configured identity."""
    risk = InMemoryRiskPolicyStore()
    with _live_client(risk, _LiveFillBroker()) as client:
        desk_id = client.post("/api/v1/desks", json={**_DESK, "i_understand_live": True}).json()[
            "desk_id"
        ]
        brief = client.get(f"/api/v1/desks/{desk_id}/briefing").json()
        outcomes = []
        for answer in ({"action": "no_trade", "thesis": "x"}, _OPEN):
            posted = client.post(
                f"/api/v1/desks/{desk_id}/wakes",
                json={
                    "raw_response": json.dumps(answer),
                    "harness": "codex",
                    "model": "unapproved",
                    "briefing_fingerprint": brief["fingerprint"],
                    "config_fingerprint": brief["config_fingerprint"],
                },
            )
            outcomes.append((posted.json()["outcome"], posted.json()["outcome_code"]))
    assert outcomes == [
        ("refused", "DESK_LIVE_IDENTITY_MISMATCH"),
        ("refused", "DESK_LIVE_IDENTITY_MISMATCH"),
    ]
