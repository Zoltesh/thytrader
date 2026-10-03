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
from thytrader.execution.decision_store import DecisionStoreError, InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentMode,
    ExecutionStoreError,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
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
        "entry_fees": None,
        "unrealized_pnl_net": None,
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


def _record_entry(world: World, bot: UUID, *, fee: str, applied: bool = True) -> None:
    """Seed a filled BTC entry with independently recorded fee evidence."""
    order = Order(
        id=uuid4(),
        deployment_id=bot,
        intent_id=uuid4(),
        client_order_id=str(uuid4()),
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("60000"),
        status=OrderStatus.FILLED,
        created_at=_BAR,
        updated_at=_BAR,
        product_id="BTC-USDC",
        filled_quantity=Decimal("0.01"),
    )
    asyncio.run(world.execution.save_order(order))
    asyncio.run(
        world.execution.save_fill(
            Fill(
                id=uuid4(),
                deployment_id=bot,
                order_id=order.id,
                venue_fill_id=str(uuid4()),
                price=Decimal("60000"),
                quantity=Decimal("0.01"),
                fee=Decimal(fee),
                filled_at=_BAR - timedelta(hours=3),
                economics_applied_at=_BAR if applied else None,
            )
        )
    )


@pytest.mark.parametrize("fee", ["0", "3"])
@pytest.mark.parametrize("mode", [DeploymentMode.PAPER, DeploymentMode.LIVE])
def test_net_books_agree_across_summary_full_and_portfolio(
    world: World, fee: str, mode: DeploymentMode
) -> None:
    """Recorded entry fees can turn gross gains negative; all marked surfaces agree."""
    portfolio, bot = _started_portfolio(world)
    _open_long(world, bot, close="60200")
    deployment = asyncio.run(world.execution.get_deployment(bot)).deployment
    asyncio.run(world.execution.save_deployment(replace(deployment, mode=mode)))
    _record_entry(world, bot, fee=fee)
    for detail in ("summary", "full"):
        response = world.client.get(f"/api/v1/deployments/{bot}?detail={detail}")
        assert response.status_code == 200, response.text
        body: JsonBody = response.json()
        for book in (*body["positions"], body["position"]):
            assert Decimal(book["entry_fees"]) == Decimal(fee)
            assert Decimal(book["unrealized_pnl"]) == Decimal("2")
            assert Decimal(book["unrealized_pnl_net"]) == Decimal("2") - Decimal(fee)
    response = world.client.get(f"/api/v1/portfolios/{portfolio}/deployment")
    assert response.status_code == 200, response.text
    book = response.json()["sleeves"][0]["deployment"]["books"][0]
    assert Decimal(book["entry_fees"]) == Decimal(fee)
    assert Decimal(book["unrealized_pnl_net"]) == Decimal("2") - Decimal(fee)


def test_unapplied_fill_does_not_establish_paid_entry_fees(world: World) -> None:
    """Fill receipt alone does not prove that the current position includes its economics."""
    portfolio, bot = _started_portfolio(world)
    _open_long(world, bot, close="60200")
    _record_entry(world, bot, fee="3", applied=False)
    book = world.client.get(f"/api/v1/deployments/{bot}").json()["positions"][0]
    sleeve = world.client.get(f"/api/v1/portfolios/{portfolio}/deployment").json()
    for row in (book, sleeve["sleeves"][0]["deployment"]["books"][0]):
        assert Decimal(row["unrealized_pnl"]) == Decimal("2")
        assert row["entry_fees"] is None
        assert row["unrealized_pnl_net"] is None


def test_short_book_net_subtracts_paid_fees_from_signed_gain(world: World) -> None:
    """Spot shorts use sell entry evidence and subtract fees even when the mark falls."""
    portfolio, bot = _started_portfolio(world)
    _open_long(world, bot, close="59800")
    snapshot = asyncio.run(world.execution.get_deployment(bot))
    assert snapshot.position is not None
    asyncio.run(
        world.execution.save_position(
            replace(snapshot.position, side=PositionSide.SHORT), deployment_id=bot
        )
    )
    _record_entry(world, bot, fee="3")
    for order in tuple(world.execution.orders.values()):
        if order.deployment_id == bot:
            asyncio.run(world.execution.save_order(replace(order, side=OrderSide.SELL)))
    for detail in ("summary", "full"):
        book = world.client.get(f"/api/v1/deployments/{bot}?detail={detail}").json()["positions"][0]
        assert Decimal(book["unrealized_pnl"]) == Decimal("2")
        assert Decimal(book["unrealized_pnl_net"]) == Decimal("-1")
    sleeve = world.client.get(f"/api/v1/portfolios/{portfolio}/deployment").json()
    book = sleeve["sleeves"][0]["deployment"]["books"][0]
    assert Decimal(book["unrealized_pnl_net"]) == Decimal("-1")


def test_fee_lookup_outage_preserves_gross_mark(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable fee read leaves net unknown without hiding a valid gross mark."""
    _portfolio, bot = _started_portfolio(world)
    _open_long(world, bot, close="60200")

    async def unavailable(position: Position, *, product_id: str) -> Decimal | None:
        """Model storage failing only during fee evidence lookup."""
        del position, product_id
        raise ExecutionStoreError("Fill evidence unavailable.")

    monkeypatch.setattr(world.execution, "get_position_entry_fees", unavailable)
    for detail in ("summary", "full"):
        response = world.client.get(f"/api/v1/deployments/{bot}?detail={detail}")
        assert response.status_code == 200, response.text
        book = response.json()["positions"][0]
        assert Decimal(book["unrealized_pnl"]) == Decimal("2")
        assert book["unrealized_pnl_net"] is None


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
    """Explicit pairs include this portfolio only; matching snapshots alone stay unpaired."""
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
    unlinked = world.client.get(f"/api/v1/portfolios/{portfolio_id}/fill-comparisons").json()
    assert unlinked["comparisons"] == []
    asyncio.run(world.execution.link_twins(paper.id, live.id))
    asyncio.run(world.execution.link_twins(outsider_paper.id, outsider_live.id))
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


@pytest.mark.parametrize("detail", ["summary", "full"])
@pytest.mark.parametrize("mode", [DeploymentMode.PAPER, DeploymentMode.LIVE])
@pytest.mark.parametrize("coverage", ["complete", "partial", "missing"])
def test_runtime_ledger_uses_each_open_books_journaled_mark(
    world: World, detail: str, mode: DeploymentMode, coverage: str
) -> None:
    """Detail and operator completeness agree, including a missing secondary short mark."""
    _portfolio_id, bot = _started_portfolio(world)
    _open_long(world, bot, close=None if coverage == "missing" else "59500")
    snapshot = asyncio.run(world.execution.get_deployment(bot))
    assert snapshot.position is not None
    short = replace(
        snapshot.position,
        product_id="ETH-USDC",
        side=PositionSide.SHORT,
        quantity=Decimal("0.2"),
        entry_price=Decimal("2500"),
        stop_price=Decimal("2600"),
        target_price=Decimal("2300"),
    )
    asyncio.run(world.execution.save_position(short, deployment_id=bot))
    # Opening both books spends 600, receives 500, and pays 2 in entry fees.
    deployment = replace(snapshot.deployment, mode=mode, cash=Decimal("398"))
    asyncio.run(world.execution.save_deployment(deployment))
    if coverage == "complete":
        decision = BarDecision(
            deployment_id=bot,
            product_id="ETH-USDC",
            timeframe="1h",
            mode=mode,
            bar_starts_at=_BAR,
            bar_closes_at=_BAR + timedelta(hours=1),
            evaluated_at=_BAR + timedelta(hours=1, seconds=2),
            outcome=DecisionOutcome.HOLDING,
            reason_code="HOLDING",
            summary="Holding short.",
            close_price="2450",
        )
        asyncio.run(world.journal.upsert(decision))

    response = world.client.get(f"/api/v1/deployments/{bot}?detail={detail}")
    assert response.status_code == 200, response.text
    body: JsonBody = response.json()
    ledger = body["ledger"]
    assert ledger["mark_complete"] is (coverage == "complete")
    if coverage == "complete":
        assert Decimal(ledger["marked_exposure"]) == Decimal("105")
        assert Decimal(ledger["total_net_pnl"]) == Decimal("3")
        assert Decimal(ledger["total_return_fraction"]) == Decimal("0.006")
    else:
        assert ledger["marked_exposure"] is None
        assert ledger["total_net_pnl"] is None
        assert ledger["total_return_fraction"] is None
    report = world.client.get(f"/api/v1/operator/runtime?deployment_id={bot}").json()
    (row,) = report["payload"]["deployments"]
    assert row["ledger_mark_complete"] is ledger["mark_complete"]


def test_runtime_marks_fail_closed_when_the_journal_is_unavailable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A journal outage leaves open books incomplete without failing either read."""
    _portfolio_id, bot = _started_portfolio(world)
    _open_long(world, bot, close="61000")

    async def unavailable(
        deployment_id: UUID, product_id: str, bar_starts_at: datetime
    ) -> BarDecision | None:
        """Model an unavailable journal after a price was previously written."""
        del deployment_id, product_id, bar_starts_at
        raise DecisionStoreError("Decision journal is unavailable.")

    monkeypatch.setattr(world.journal, "latest_before", unavailable)
    for detail in ("summary", "full"):
        response = world.client.get(f"/api/v1/deployments/{bot}?detail={detail}")
        assert response.status_code == 200, response.text
        body: JsonBody = response.json()
        assert body["ledger"]["mark_complete"] is False
        assert body["ledger"]["total_net_pnl"] is None
        assert body["positions"][0]["mark_price"] is None
    report = world.client.get(f"/api/v1/operator/runtime?deployment_id={bot}").json()
    (row,) = report["payload"]["deployments"]
    assert row["ledger_mark_complete"] is False
