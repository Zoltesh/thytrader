"""HTTP execution-quality evidence for one book and its explicit twin (ADR 0116)."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.execution.store import DisabledExecutionStore

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
FINGERPRINT = "sha256:" + "b" * 64


def _deployment(
    mode: DeploymentMode = DeploymentMode.PAPER,
    *,
    product_id: str = "BTC-USD",
) -> Deployment:
    """One strategy deployment the in-memory store can hold."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=FINGERPRINT,
        strategy_id=uuid4(),
        product_id=product_id,
        mode=mode,
        status=DeploymentStatus.STOPPED,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=START,
        updated_at=START,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
        strategy_name="Quality",
        paper_maker_fee_rate=None if mode is DeploymentMode.LIVE else Decimal("0.001"),
        paper_taker_fee_rate=None if mode is DeploymentMode.LIVE else Decimal("0.002"),
    )


def _round_trip(
    deployment: Deployment, *, at: datetime, exit_price: str
) -> tuple[Order, Order, Fill, Fill]:
    """One applied maker buy and marketable sell."""
    buy = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id=f"b{uuid4().hex[:8]}",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=at,
        updated_at=at,
        price=Decimal("100"),
    )
    sell = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id=f"s{uuid4().hex[:8]}",
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=at + HOUR,
        updated_at=at + HOUR,
        price=Decimal(exit_price),
    )
    buy_fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=buy.id,
        venue_fill_id=f"vb{uuid4().hex[:8]}",
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        filled_at=at,
        economics_applied_at=at,
    )
    sell_fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=sell.id,
        venue_fill_id=f"vs{uuid4().hex[:8]}",
        price=Decimal(exit_price),
        quantity=Decimal("1"),
        fee=Decimal("0.11"),
        filled_at=at + HOUR,
        economics_applied_at=at + HOUR,
    )
    return buy, sell, buy_fill, sell_fill


async def _seed(
    store: InMemoryExecutionStore, deployment: Deployment, *, exit_price: str = "110"
) -> None:
    """Persist one closed round trip."""
    await store.create_deployment(deployment)
    buy, sell, buy_fill, sell_fill = _round_trip(deployment, at=START, exit_price=exit_price)
    await store.save_order(buy)
    await store.save_order(sell)
    await store.save_fill(buy_fill)
    await store.save_fill(sell_fill)


def _decision(deployment: Deployment, bar: datetime, close: str) -> BarDecision:
    """One journaled close for the bar containing a fill."""
    return BarDecision(
        deployment_id=deployment.id,
        product_id=deployment.product_id,
        timeframe="1h",
        mode=deployment.mode,
        bar_starts_at=bar,
        bar_closes_at=bar + HOUR,
        evaluated_at=bar + HOUR,
        outcome=DecisionOutcome.HOLDING,
        reason_code="HOLDING",
        summary="Holding through this bar.",
        close_price=close,
    )


def test_execution_quality_reports_recorded_round_trip_fees() -> None:
    """The read route returns exact recorded fees and does not invent a missing close."""
    store = InMemoryExecutionStore()
    deployment = _deployment()
    asyncio.run(_seed(store, deployment))
    app = create_app(
        Settings(_env_file=None),
        execution_store=store,
        decision_journal_store=InMemoryDecisionJournalStore(),
    )
    with TestClient(app) as client:
        response = client.get(f"/api/v1/deployments/{deployment.id}/execution-quality")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "thytrader-execution-quality-v1"
    assert body["totals"]["net_pnl"] == "9.79"
    assert body["totals"]["entry_fees"] == "0.1"
    assert body["totals"]["exit_fees"] == "0.11"
    assert body["evidence"]["complete"] is False
    assert "fill_without_journaled_close" in body["evidence"]["reasons"]
    trip = body["books"][0]["round_trips"][0]
    assert trip["entries"][0]["slippage_bps"] is None
    assert trip["entries"][0]["liquidity"] == "maker"
    assert trip["exits"][0]["liquidity"] == "taker"


def test_execution_quality_uses_journaled_closes_when_present() -> None:
    """A journaled close becomes slippage evidence; it is never treated as a fee."""
    store = InMemoryExecutionStore()
    journal = InMemoryDecisionJournalStore()
    deployment = _deployment()

    async def prepare() -> None:
        await _seed(store, deployment)
        await journal.upsert(_decision(deployment, START, "101"))
        await journal.upsert(_decision(deployment, START + HOUR, "109"))

    asyncio.run(prepare())
    app = create_app(
        Settings(_env_file=None),
        execution_store=store,
        decision_journal_store=journal,
    )
    with TestClient(app) as client:
        response = client.get(f"/api/v1/deployments/{deployment.id}/execution-quality")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["complete"] is True
    trip = body["books"][0]["round_trips"][0]
    assert trip["slippage_fills_journaled"] == 2
    assert trip["entries"][0]["slippage_bps"] is not None


def test_execution_quality_maps_missing_books_and_disabled_storage() -> None:
    """Unknown deployments are 404; a disabled store is 503 and changes nothing."""
    store = InMemoryExecutionStore()
    app = create_app(Settings(_env_file=None), execution_store=store)
    missing = uuid4()
    with TestClient(app) as client:
        missing_response = client.get(f"/api/v1/deployments/{missing}/execution-quality")
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"]["code"] == "deployment_not_found"
    disabled = create_app(Settings(_env_file=None), execution_store=DisabledExecutionStore())
    with TestClient(disabled) as client:
        unavailable = client.get(f"/api/v1/deployments/{missing}/execution-quality")
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "execution_quality_unavailable"


def test_execution_twin_requires_an_explicit_link_and_does_not_rewrite_pnl() -> None:
    """The twin route compares only a saved pair and keeps observed fees distinct."""
    store = InMemoryExecutionStore()
    paper = _deployment()
    live = _deployment(DeploymentMode.LIVE)

    async def prepare() -> None:
        await _seed(store, paper, exit_price="110")
        await _seed(store, live, exit_price="108")
        await store.link_twins(paper.id, live.id)

    asyncio.run(prepare())
    app = create_app(Settings(_env_file=None), execution_store=store)
    with TestClient(app) as client:
        unlinked = client.get(f"/api/v1/deployments/{uuid4()}/execution-quality/twin")
        linked = client.get(f"/api/v1/deployments/{paper.id}/execution-quality/twin")
    assert unlinked.status_code == 404
    assert unlinked.json()["detail"]["code"] == "execution_twin_not_linked"
    assert linked.status_code == 200
    body = linked.json()
    assert body["schema_version"] == "thytrader-execution-twin-comparison-v1"
    assert body["comparable"] is False
    assert "incomplete_paper_evidence" in body["reasons"]
    assert body["paper"]["net_pnl"] == "9.79"
    assert body["live"]["net_pnl"] == "7.79"
    normalization = body["fee_normalization"]
    assert normalization["observed_live_fees"] == "0.21"
    assert normalization["counterfactual_live_fees_at_paper_rates"] == "0.316"
    assert body["live"]["net_pnl"] == "7.79"


def test_execution_quality_does_not_mutate_the_store() -> None:
    """Reading the report leaves orders, fills, and links unchanged."""
    store = InMemoryExecutionStore()
    deployment = _deployment()
    asyncio.run(_seed(store, deployment))
    before = asyncio.run(store.get_deployment(deployment.id))
    app = create_app(Settings(_env_file=None), execution_store=store)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/deployments/{deployment.id}/execution-quality")
    after = asyncio.run(store.get_deployment(deployment.id))
    assert response.status_code == 200
    assert len(after.fills) == len(before.fills) == 2
    assert len(after.orders) == len(before.orders) == 2
    assert asyncio.run(store.get_twin_link(deployment.id)) is None
