"""HTTP contracts for agent desks (ADR 0147): create, brief, wake, journal and pause."""

from __future__ import annotations

import asyncio
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


def _client(
    execution: InMemoryExecutionStore | None = None,
    *,
    audit: InMemoryAuditEventStore | None = None,
) -> TestClient:
    """An API with in-memory desks and execution on demo candles."""
    app = create_app(
        Settings(_env_file=None),
        strategy_store=DisabledStrategyStore(),
        execution_store=execution or InMemoryExecutionStore(),
        paper_broker=PaperBroker(),
        audit_event_store=audit or InMemoryAuditEventStore(),
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
            "config_fingerprint": brief.json()["config_fingerprint"],
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


def test_an_unchanged_wake_is_skipped_over_http_and_journaled() -> None:
    """After an answered wake, an unchanged picture journals a skip; a new model never skips."""
    with _client() as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        first = client.post(f"/api/v1/desks/{desk_id}/wakes/skip", json={"harness": "claude"})
        brief = client.get(f"/api/v1/desks/{desk_id}/briefing").json()
        answered = client.post(
            f"/api/v1/desks/{desk_id}/wakes",
            json={
                "raw_response": json.dumps({"action": "no_trade", "thesis": "flat"}),
                "harness": "claude",
                "briefing_fingerprint": brief["fingerprint"],
                "material_fingerprint": brief["material_fingerprint"],
                "config_fingerprint": brief["config_fingerprint"],
            },
        )
        skipped = client.post(f"/api/v1/desks/{desk_id}/wakes/skip", json={"harness": "claude"})
        other = client.post(
            f"/api/v1/desks/{desk_id}/wakes/skip", json={"harness": "claude", "model": "m2"}
        )
        journal = client.get(f"/api/v1/desks/{desk_id}/wakes").json()
    assert first.status_code == 200, first.text
    assert first.json()["skipped"] is False
    assert first.json()["briefing"]["config_fingerprint"] == brief["config_fingerprint"]
    assert first.json()["briefing"]["harness"] == "claude"
    assert answered.status_code == 201, answered.text
    assert skipped.json()["skipped"] is True
    assert skipped.json()["wake"]["outcome"] == "skipped"
    assert other.json()["skipped"] is False
    assert other.json()["reason"].startswith("DESK_WAKE_HARNESS_CHANGED")
    assert [wake["outcome"] for wake in journal] == ["skipped", "applied"]


def test_a_desk_update_is_saved_and_audited_and_its_mode_cannot_change() -> None:
    """PATCH changes the model under a new revision and records who changed what."""
    audit = InMemoryAuditEventStore()
    with _client(audit=audit) as client:
        desk = client.post("/api/v1/desks", json={**_DESK, "model": "old"}).json()
        updated = client.patch(
            f"/api/v1/desks/{desk['desk_id']}",
            json={"model": "new", "expected_revision": desk["revision"]},
        )
        stale = client.patch(
            f"/api/v1/desks/{desk['desk_id']}",
            json={"model": "newer", "expected_revision": desk["revision"]},
        )
        mode = client.patch(f"/api/v1/desks/{desk['desk_id']}", json={"mode": "live"})
        missing = client.patch("/api/v1/desks/missing", json={"model": "x"})
        events = asyncio.run(audit.list_recent())
    assert updated.status_code == 200, updated.text
    assert updated.json()["model"] == "new"
    assert updated.json()["revision"] == desk["revision"] + 1
    assert stale.status_code == 409
    assert "DESK_CONFLICT" in stale.text
    assert mode.status_code == 422
    assert missing.status_code == 404
    assert [event.action for event in events] == ["desk_updated"]
    assert "model: old -> new" in events[0].detail


def test_a_status_change_is_audited() -> None:
    """Pause and resume each append a desk_status_changed audit event."""
    audit = InMemoryAuditEventStore()
    with _client(audit=audit) as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "paused"})
        client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "active"})
        events = asyncio.run(audit.list_recent())
    details = sorted(event.detail for event in events if event.action == "desk_status_changed")
    assert len(details) == 2
    assert any("status: active -> paused" in detail for detail in details)
    assert any("status: paused -> active" in detail for detail in details)


def test_a_status_change_is_kept_on_the_desk_row() -> None:
    """Pause and resume are in the desk's change history, written with the change."""
    with _client() as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "paused"})
        resumed = client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "active"})
    history = [entry["changed"] for entry in resumed.json()["changes"]]
    assert history == [{"status": ["active", "paused"]}, {"status": ["paused", "active"]}]


def test_a_wake_start_must_be_aware_and_is_never_later_than_now() -> None:
    """A naive start is a 422; a future one is journaled at the server's now."""
    with _client() as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        brief = client.get(f"/api/v1/desks/{desk_id}/briefing").json()
        body = {
            "raw_response": json.dumps({"action": "no_trade", "thesis": "flat"}),
            "harness": "claude",
            "briefing_fingerprint": brief["fingerprint"],
            "config_fingerprint": brief["config_fingerprint"],
        }
        naive = client.post(
            f"/api/v1/desks/{desk_id}/wakes", json={**body, "started_at": "2026-10-28T12:00:00"}
        )
        future = client.post(
            f"/api/v1/desks/{desk_id}/wakes",
            json={**body, "started_at": "2099-01-01T00:00:00Z"},
        )
        skip = client.post(
            f"/api/v1/desks/{desk_id}/wakes/skip",
            json={"harness": "claude", "started_at": "2026-10-28T12:00:00"},
        )
    assert naive.status_code == 422
    assert skip.status_code == 422
    assert future.status_code == 201, future.text
    assert future.json()["started_at"] <= future.json()["finished_at"]


def test_resuming_a_desk_restarts_its_stall_clock() -> None:
    """A resume moves due_since forward; a pause leaves it."""
    with _client() as client:
        desk_id = client.post("/api/v1/desks", json=_DESK).json()["desk_id"]
        paused = client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "paused"}).json()
        resumed = client.post(f"/api/v1/desks/{desk_id}/status", json={"status": "active"}).json()
    assert paused["due_since"] is not None
    assert resumed["due_since"] > paused["due_since"]


def test_setting_the_status_a_desk_already_has_writes_nothing() -> None:
    """A repeated `status active` neither bumps the revision nor fills the change history."""
    with _client() as client:
        desk = client.post("/api/v1/desks", json=_DESK).json()
        again = client.post(f"/api/v1/desks/{desk['desk_id']}/status", json={"status": "active"})
    assert again.status_code == 200, again.text
    assert again.json()["revision"] == desk["revision"]
    assert again.json()["changes"] == []
