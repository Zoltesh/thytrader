"""HTTP contracts for agent desks (ADR 0147): create, brief, wake, journal and pause."""

from __future__ import annotations

from decimal import Decimal
import json

from fastapi.testclient import TestClient

from thytrader.agent_desk.store import InMemoryDeskStore
from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentKind


def _client(execution: InMemoryExecutionStore | None = None) -> TestClient:
    """An API with in-memory desks and execution on demo candles."""
    app = create_app(
        Settings(_env_file=None),
        strategy_store=DisabledStrategyStore(),
        execution_store=execution or InMemoryExecutionStore(),
        paper_broker=PaperBroker(),
        audit_event_store=InMemoryAuditEventStore(),
        market_data_service=MarketDataService(DemoMarketData()),
    )
    app.state.desk_store = InMemoryDeskStore()
    return TestClient(app)


_DESK = {
    "name": "http desk",
    "quote_currency": "USD",
    "starting_cash": "10000",
    "products": ["BTC-USD", "ETH-USD"],
    "harness": "claude",
}


def _wake(client: TestClient, desk_id: str, answer: object) -> dict:
    """Read the briefing, then post ``answer`` against its fingerprint."""
    brief = client.get(f"/api/v1/desks/{desk_id}/briefing")
    assert brief.status_code == 200, brief.text
    posted = client.post(
        f"/api/v1/desks/{desk_id}/wakes",
        json={
            "raw_response": answer if isinstance(answer, str) else json.dumps(answer),
            "harness": "claude",
            "briefing_fingerprint": brief.json()["fingerprint"],
        },
    )
    assert posted.status_code == 201, posted.text
    return posted.json()


def test_a_desk_is_created_listed_and_briefed() -> None:
    """The briefing carries the pool, the account fee rates and the prompt to hand a harness."""
    with _client() as client:
        created = client.post("/api/v1/desks", json=_DESK)
        assert created.status_code == 201, created.text
        desk_id = created.json()["desk_id"]
        listed = client.get("/api/v1/desks").json()
        brief = client.get(f"/api/v1/desks/{desk_id}/briefing").json()
    assert [desk["desk_id"] for desk in listed] == [desk_id]
    assert Decimal(brief["desk"]["equity_quote"]) == Decimal(10000)
    assert brief["desk"]["taker_fee_rate"] is not None
    assert brief["fingerprint"] in brief["prompt"]


def test_an_open_answer_funds_a_discretionary_paper_book() -> None:
    """A valid open is applied; the desk shows the book and the journal shows the wake."""
    execution = InMemoryExecutionStore()
    with _client(execution) as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        wake = _wake(
            client,
            desk_id,
            {
                "action": "open",
                "thesis": "breakout",
                "product_id": "BTC-USD",
                "side": "long",
                "size_fraction": "0.1",
                "entry": "market",
                "stop_price": "1",
                "take_profit_price": "100000000",
            },
        )
        detail = client.get(f"/api/v1/desks/{desk_id}").json()
        journal = client.get(f"/api/v1/desks/{desk_id}/wakes").json()
        deployments = client.get("/api/v1/deployments").json()["deployments"]
    assert wake["outcome"] == "applied", wake
    assert Decimal(detail["desk"]["cash"]) == Decimal(9000)
    assert [book["deployment_id"] for book in detail["books"]] == [wake["deployment_id"]]
    assert [entry["wake_id"] for entry in journal] == [wake["wake_id"]]
    assert [row["kind"] for row in deployments] == [DeploymentKind.DISCRETIONARY.value]


def test_a_refused_or_garbled_answer_is_journaled_not_an_http_error() -> None:
    """The harness posted successfully; ThyTrader refused the decision and said why."""
    with _client() as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        garbled = _wake(client, desk_id, "buy everything")
        refused = _wake(
            client,
            desk_id,
            {
                "action": "open",
                "thesis": "too big",
                "product_id": "BTC-USD",
                "side": "long",
                "size_fraction": "0.9",
                "entry": "market",
                "stop_price": "1",
                "take_profit_price": "100000000",
            },
        )
    assert (garbled["outcome"], garbled["outcome_code"]) == ("invalid", "DESK_DECISION_INVALID")
    assert (refused["outcome"], refused["outcome_code"]) == ("refused", "DESK_POSITION_TOO_LARGE")


def test_a_paused_desk_refuses_trades_and_unknown_desks_are_404() -> None:
    """Status changes are persisted; a missing desk is 404 on every per-desk route."""
    with _client() as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        paused = client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "paused"})
        wake = _wake(
            client,
            desk_id,
            {
                "action": "open",
                "thesis": "x",
                "product_id": "BTC-USD",
                "side": "long",
                "size_fraction": "0.1",
                "entry": "market",
                "stop_price": "1",
                "take_profit_price": "100000000",
            },
        )
        missing = client.get("/api/v1/desks/nope/briefing")
        missing_status = client.post("/api/v1/desks/nope/status", json={"status": "paused"})
    assert paused.json()["status"] == "paused"
    assert wake["outcome_code"] == "DESK_NOT_ACTIVE"
    assert missing.status_code == 404
    assert missing_status.status_code == 404


def test_a_universe_outside_the_quote_currency_is_refused_with_409() -> None:
    """Creation validates the universe; nothing is stored."""
    with _client() as client:
        refused = client.post("/api/v1/desks", json={**_DESK, "products": ["BTC-USDC"]})
        listed = client.get("/api/v1/desks").json()
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("DESK_PRODUCT_INVALID")
    assert listed == []
