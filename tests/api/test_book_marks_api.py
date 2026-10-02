"""Marked open books and the per-portfolio paper vs live fill comparison (ADR 0098).

Deployment and portfolio reads price each open book at the close of the newest bar the
bot evaluated (the decision journal), so bot detail and the Portfolio page can show
unrealized PnL without a venue call. The portfolio's ``fill-comparisons`` route carries
the operator report's paper/live twin rows for that portfolio only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentMode,
    Position,
    PositionSide,
)
from thytrader.portfolios.store import InMemoryPortfolioStore
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from collections.abc import Iterator

JsonBody = dict[str, Any]
"""A decoded TestClient JSON body (dynamic at this HTTP boundary; asserted field by field)."""

_BAR = datetime(2026, 10, 1, 11, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class World:
    """One app plus the stores the tests seed directly."""

    client: TestClient
    strategies: InMemoryStrategyStore
    execution: InMemoryExecutionStore
    journal: InMemoryDecisionJournalStore


@pytest.fixture
def world() -> Iterator[World]:
    """An app with in-memory strategies, deployments, portfolios, journal, and risk policy."""
    strategies = InMemoryStrategyStore()
    execution = InMemoryExecutionStore()
    journal = InMemoryDecisionJournalStore()
    risk = InMemoryRiskPolicyStore()
    asyncio.run(risk.publish(compiled_default_risk_policy()))
    app = create_app(
        Settings(_env_file=None),
        strategy_store=strategies,
        execution_store=execution,
        risk_policy_store=risk,
        decision_journal_store=journal,
        portfolio_store=InMemoryPortfolioStore(strategies=strategies, execution=execution),
    )
    with TestClient(app) as client:
        yield World(client, strategies, execution, journal)


def _started_portfolio(world: World) -> tuple[str, UUID]:
    """A paper portfolio with one BTC-USDC sleeve, started; returns its id and the bot id."""
    created = world.client.post(
        "/api/v1/portfolios",
        json={"name": "Core", "mode": "paper", "capital_quote": "1000"},
    )
    assert created.status_code == 201, created.text
    body: JsonBody = created.json()
    definition = create_template_strategy(
        product_id="BTC-USDC", timeframe="1h", template="ema-trend"
    )
    record = asyncio.run(create_strategy_from_definition(world.strategies, definition))
    added = world.client.post(
        f"/api/v1/portfolios/{body['portfolio_id']}/sleeves",
        json={
            "revision": body["revision"],
            "strategy_id": str(record.strategy_id),
            "weight_fraction": "0.5",
        },
    )
    assert added.status_code == 201, added.text
    body = added.json()
    started = world.client.post(
        f"/api/v1/portfolios/{body['portfolio_id']}/start", json={"revision": body["revision"]}
    )
    assert started.status_code == 200, started.text
    (sleeve,) = started.json()["deployment"]["sleeves"]
    return body["portfolio_id"], UUID(sleeve["deployment"]["deployment_id"])


def _open_long(world: World, deployment_id: UUID, *, close: str | None) -> None:
    """Hold 0.01 BTC from 60,000 and journal the last evaluated bar's close."""
    position = Position(
        deployment_id=deployment_id,
        quantity=Decimal("0.01"),
        entry_price=Decimal("60000"),
        stop_price=Decimal("58000"),
        target_price=Decimal("64000"),
        entered_bar=_BAR - timedelta(hours=3),
        updated_at=_BAR,
        side=PositionSide.LONG,
        product_id="BTC-USDC",
    )
    asyncio.run(world.execution.save_position(position, deployment_id=deployment_id))
    decision = BarDecision(
        deployment_id=deployment_id,
        product_id="BTC-USDC",
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        bar_starts_at=_BAR,
        bar_closes_at=_BAR + timedelta(hours=1),
        evaluated_at=_BAR + timedelta(hours=1, seconds=2),
        outcome=DecisionOutcome.NO_SIGNAL,
        reason_code="NO_SIGNAL",
        summary="Holding.",
        close_price=close,
    )
    asyncio.run(world.journal.upsert(decision))


def test_sleeve_books_carry_entry_stop_target_state_and_last_bar_pnl(world: World) -> None:
    """A sleeve bot lists its open book, marked at the last journaled close."""
    portfolio_id, bot = _started_portfolio(world)
    _open_long(world, bot, close="61000")
    view = world.client.get(f"/api/v1/portfolios/{portfolio_id}/deployment").json()
    (book,) = view["sleeves"][0]["deployment"]["books"]
    assert book == {
        "product_id": "BTC-USDC",
        "side": "long",
        "quantity": "0.01",
        "entry_price": "60000",
        "stop_price": "58000",
        "target_price": "64000",
        "entered_bar": "2026-10-01T08:00:00Z",
        "position_state": "open_protected",
        "mark_price": "61000",
        "marked_at": "2026-10-01T12:00:00Z",
        "unrealized_pnl": "10",
    }


def test_bot_detail_positions_carry_the_last_bar_mark(world: World) -> None:
    """``GET /deployments/{id}`` marks positions on summary and full reads alike."""
    _portfolio_id, bot = _started_portfolio(world)
    _open_long(world, bot, close="59500")
    for detail in ("summary", "full"):
        body = world.client.get(f"/api/v1/deployments/{bot}?detail={detail}").json()
        (position,) = body["positions"]
        assert position["mark_price"] == "59500"
        assert Decimal(position["unrealized_pnl"]) == Decimal("-5")
        assert position["marked_at"].startswith("2026-10-01T12:00:00")
        # ADR 0098: a paper book is covered, as its position_state says.
        assert (position["protection_status"], position["position_state"]) == (
            "covered",
            "open_protected",
        )


def test_books_without_a_journaled_close_stay_unmarked(world: World) -> None:
    """No close means no mark and no unrealized PnL; nothing is invented."""
    portfolio_id, bot = _started_portfolio(world)
    _open_long(world, bot, close=None)
    view = world.client.get(f"/api/v1/portfolios/{portfolio_id}/deployment").json()
    (book,) = view["sleeves"][0]["deployment"]["books"]
    assert (book["mark_price"], book["unrealized_pnl"]) == (None, None)
    position = world.client.get(f"/api/v1/deployments/{bot}").json()["positions"][0]
    assert (position["mark_price"], position["unrealized_pnl"]) == (None, None)


def test_fill_comparisons_list_only_this_portfolios_twins(world: World) -> None:
    """A live twin of a sleeve pairs; twins outside the portfolio are left out."""
    portfolio_id, bot = _started_portfolio(world)
    paper = asyncio.run(world.execution.get_deployment(bot)).deployment
    live = replace(paper, id=uuid4(), mode=DeploymentMode.LIVE, portfolio_id=None, created_at=_BAR)
    asyncio.run(world.execution.create_deployment(live))
    outsider_paper = replace(
        paper, id=uuid4(), strategy_fingerprint="sha256:" + ("e" * 64), portfolio_id=None
    )
    outsider_live = replace(outsider_paper, id=uuid4(), mode=DeploymentMode.LIVE)
    for item in (outsider_paper, outsider_live):
        asyncio.run(world.execution.create_deployment(item))
    body = world.client.get(f"/api/v1/portfolios/{portfolio_id}/fill-comparisons").json()
    assert body["portfolio_id"] == portfolio_id
    (row,) = body["comparisons"]
    assert row["strategy_fingerprint"] == paper.strategy_fingerprint
    assert (row["paper"]["deployment_id"], row["live"]["deployment_id"]) == (
        str(bot),
        str(live.id),
    )
    assert row["paper"]["portfolio_id"] == portfolio_id
    assert row["live"]["entries_rested"] == 0
    assert body["warnings"] == []
    missing = world.client.get(f"/api/v1/portfolios/{uuid4()}/fill-comparisons")
    assert missing.status_code == 404
