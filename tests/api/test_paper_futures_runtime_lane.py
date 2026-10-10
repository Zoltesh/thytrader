"""The paper futures runtime lane end to end over HTTP (ADR 0129 §4, P1-6).

Start a perp strategy as a paper book through ``POST /api/v1/deployments`` (live is
refused with ``FUTURES_LIVE_UNSUPPORTED`` and creates nothing), let the worker's bar loop
fill whole contracts, read ``GET /api/v1/deployments/{id}/futures`` and the operator
``futures-books`` report, then stop with ``flatten`` and see the book flat. Funding that
was never recorded is reported overdue (unknown) and denies entries; it is never zero.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from tests.execution.test_paper_futures_books import (
    _MARGIN,
    _PERP,
    _observation,
    _policy,
    _product,
    _strategy,
)
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution import futures_start, service
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.futures_start import FuturesStart
from thytrader.execution.loop import maintain_open_inventory, process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.execution.residual import flatten_stopped_residual
from thytrader.market_data.models import Candle
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.futures_book import (
    FuturesBookState,
    InMemoryFuturesContractStore,
    futures_book_scope,
)
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, IntentPurpose, RuntimePhase

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.market_data.futures_observations import (
        FundingRateRecord,
        FuturesInstrumentObservation,
    )
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot

JsonBody = dict[str, Any]
"""A decoded TestClient JSON body (dynamic at this HTTP boundary; asserted field by field)."""

_OPEN = datetime(2026, 3, 2, tzinfo=UTC)
"""The paper start instant; every bar after it is in the past for the reporting clock."""

_RATES = {"overnight_long_margin_rate": "0.2", "overnight_short_margin_rate": "0.25"}


class _Catalog:
    """The recorded BIP perp facts with known overnight rates and no funding rows."""

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Return the rated perp observation."""
        if product_id != _PERP:
            return None
        return replace(_observation(), **_RATES), _OPEN

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """No funding history was recorded."""
        del product_id, starts_at, ends_at
        return ()


@dataclass(frozen=True, slots=True)
class Lane:
    """One app plus the stores the worker steps read and write."""

    client: TestClient
    strategy: StrategyDefinition
    execution: InMemoryExecutionStore
    journal: InMemoryDecisionJournalStore
    contracts: InMemoryFuturesContractStore
    strategies: InMemoryStrategyStore


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> Iterator[Lane]:
    """An app with the perp strategy, the futures policy and in-memory futures stores."""
    monkeypatch.setattr(service, "utc_now", lambda: _OPEN)
    monkeypatch.setattr(futures_start, "utc_now", lambda: _OPEN)
    strategy = _strategy()
    strategies = InMemoryStrategyStore()
    asyncio.run(create_strategy_from_definition(strategies, strategy))
    execution = InMemoryExecutionStore()
    journal = InMemoryDecisionJournalStore()
    risk = InMemoryRiskPolicyStore()
    asyncio.run(risk.publish(_policy()))
    contracts = InMemoryFuturesContractStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_store=strategies,
        execution_store=execution,
        risk_policy_store=risk,
        decision_journal_store=journal,
    )
    app.state.futures_start = FuturesStart(contracts=contracts, observations=_Catalog())
    with TestClient(app) as client:
        yield Lane(client, strategy, execution, journal, contracts, strategies)


def _start_body(strategy_id: UUID, **changes: object) -> JsonBody:
    """A paper futures start with USD cash and all three fees."""
    body: JsonBody = {
        "strategy_id": str(strategy_id),
        "mode": "paper",
        "paper_starting_cash": "10000",
        "maker_fee_rate": "0",
        "taker_fee_rate": "0.0005",
        "paper_fee_per_contract": "0.15",
    }
    body.update(changes)
    return body


def _candles(count: int) -> tuple[Candle, ...]:
    """A rising 1h series from the start instant (closes 100, 101, ...)."""
    return tuple(
        Candle(
            starts_at=_OPEN + timedelta(hours=index),
            open=Decimal(100 + index),
            high=Decimal(100 + index) + 2,
            low=Decimal(100 + index) - 1,
            close=Decimal(100 + index),
            volume=Decimal(10),
        )
        for index in range(count)
    )


async def _bar(lane: Lane, snapshot: DeploymentSnapshot, bars: int) -> DeploymentSnapshot:
    """Run the worker's closed-bar step under the bound book, then journal its close."""
    binding = await lane.contracts.load_contract(snapshot.deployment.id)
    assert binding is not None
    state = FuturesBookState(
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        side="long",
        binding=binding,
        margin=_MARGIN,
    )
    candles = _candles(bars)
    with futures_book_scope(state):
        after = await process_closed_bar(
            snapshot,
            strategy=lane.strategy,
            product=_product(),
            candles=candles,
            broker=PaperBroker(),
            store=lane.execution,
            risk_policy=_policy(),
        )
    await _journal_close(lane, snapshot, candles[-1])
    return after


async def _journal_close(lane: Lane, snapshot: DeploymentSnapshot, candle: Candle) -> None:
    """Journal the evaluated bar's close, as the worker's bar journal does."""
    await lane.journal.upsert(
        BarDecision(
            deployment_id=snapshot.deployment.id,
            product_id=_PERP,
            timeframe="1h",
            mode=snapshot.deployment.mode,
            bar_starts_at=candle.starts_at,
            bar_closes_at=candle.starts_at + timedelta(hours=1),
            evaluated_at=candle.starts_at + timedelta(hours=1, seconds=2),
            outcome=DecisionOutcome.NO_SIGNAL,
            reason_code="NO_SIGNAL",
            summary="Holding.",
            close_price=str(candle.close),
        )
    )


def test_live_futures_starts_are_refused_and_create_nothing(lane: Lane) -> None:
    """Unacknowledged live is 428 as for spot; acknowledged live is FUTURES_LIVE_UNSUPPORTED."""
    strategy_id = lane.strategy.strategy_id
    unacknowledged = lane.client.post(
        "/api/v1/deployments", json={"strategy_id": str(strategy_id), "mode": "live"}
    )
    assert unacknowledged.status_code == 428
    refused = lane.client.post(
        "/api/v1/deployments",
        json={"strategy_id": str(strategy_id), "mode": "live", "i_understand_live": True},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("FUTURES_LIVE_UNSUPPORTED")
    adopting = lane.client.post(
        "/api/v1/deployments",
        json={
            "strategy_id": str(strategy_id),
            "mode": "live",
            "adopt_holdings": "all",
            "i_understand_live": True,
        },
    )
    assert adopting.status_code == 409
    assert adopting.json()["detail"].startswith("FUTURES_LIVE_UNSUPPORTED")
    missing_fee = lane.client.post(
        "/api/v1/deployments", json=_start_body(strategy_id, paper_fee_per_contract=None)
    )
    assert missing_fee.status_code == 409
    assert "FUTURES_FEE_REQUIRED" in missing_fee.json()["detail"]
    assert lane.execution.deployments == {}
    assert lane.contracts.bindings == {}


def test_a_paper_futures_book_runs_reports_and_flattens(lane: Lane) -> None:
    """Start, fill whole contracts, read the futures view and report, stop with flatten."""
    started = lane.client.post("/api/v1/deployments", json=_start_body(lane.strategy.strategy_id))
    assert started.status_code == 201, started.text
    deployment_id = UUID(started.json()["id"])
    snapshot = asyncio.run(lane.execution.get_deployment(deployment_id))
    rested = asyncio.run(_bar(lane, snapshot, 30))
    assert rested.deployment.phase is RuntimePhase.PENDING_ENTRY
    filled = asyncio.run(_bar(lane, rested, 31))
    assert filled.position is not None
    quantity = filled.position.quantity
    assert quantity % Decimal("0.01") == 0

    view: JsonBody = lane.client.get(f"/api/v1/deployments/{deployment_id}/futures").json()
    assert view["product_id"] == _PERP
    assert view["currency"] == "USD"
    assert view["contract_kind"] == "perpetual_future"
    assert view["contract_size"] == "0.01"
    assert view["fee_per_contract"] == "0.15"
    assert view["side"] == "long"
    assert Decimal(view["contracts"]) == quantity / Decimal("0.01")
    assert view["mark_price"] == "130"
    stored = asyncio.run(lane.execution.get_deployment(deployment_id))
    equity = stored.deployment.cash + quantity * Decimal(130)
    assert Decimal(view["equity"]) == equity
    assert Decimal(view["initial_margin"]) == quantity * Decimal(130) * Decimal("0.2")
    assert Decimal(view["leverage"]) <= Decimal(2)
    assert Decimal(view["liquidation_buffer_fraction"]) >= Decimal("0.5")
    # No funding was ever recorded for the hours the book held: unknown, and entries wait.
    assert view["funding_total"] == "0"
    assert view["funding_overdue_since"] is not None
    assert "FUNDING_HISTORY_MISSING" in view["entry_blocks"]
    assert view["unknown"] == ["funding"]

    report: JsonBody = lane.client.get("/api/v1/operator/futures-books").json()
    assert report["report_kind"] == "futures_books"
    assert report["payload"]["paper_capital_usd"] == "100000"
    assert report["payload"]["committed_paper_cash_usd"] == "10000"
    assert report["payload"]["live_supported"] is False
    (book,) = report["payload"]["books"]
    assert book["deployment_id"] == str(deployment_id)
    assert book["equity"] == view["equity"]
    assert report["components"][0]["reason_code"] == "FUTURES_EVIDENCE_UNKNOWN"

    stopped = lane.client.post(f"/api/v1/deployments/{deployment_id}/stop?flatten=true")
    assert stopped.status_code == 200, stopped.text
    flat = asyncio.run(
        flatten_stopped_residual(
            asyncio.run(lane.execution.get_deployment(deployment_id)),
            strategy=lane.strategy,
            product=_product(),
            candles=_candles(32),
            broker=PaperBroker(),
            store=lane.execution,
        )
    )
    assert flat.position is None
    assert flat.deployment.status is DeploymentStatus.STOPPED
    closed: JsonBody = lane.client.get(f"/api/v1/deployments/{deployment_id}/futures").json()
    assert (closed["side"], closed["contracts"], closed["status"]) == ("flat", "0", "stopped")
    assert closed["notional"] == "0"
    assert closed["liquidation_price"] is None


def test_the_futures_view_is_404_for_spot_and_unknown_bots(lane: Lane) -> None:
    """Only futures books have a futures view."""
    spot = create_template_strategy(product_id="BTC-USDC", timeframe="1h", template="ema-trend")
    asyncio.run(create_strategy_from_definition(lane.strategies, spot))
    started = lane.client.post(
        "/api/v1/deployments",
        json={
            "strategy_id": str(spot.strategy_id),
            "mode": "paper",
            "paper_starting_cash": "1000",
            "maker_fee_rate": "0.0025",
            "taker_fee_rate": "0.004",
        },
    )
    assert started.status_code == 201, started.text
    spot_view = lane.client.get(f"/api/v1/deployments/{started.json()['id']}/futures")
    assert spot_view.status_code == 404
    unknown = lane.client.get(f"/api/v1/deployments/{UUID(int=7)}/futures")
    assert unknown.status_code == 404
    report: JsonBody = lane.client.get("/api/v1/operator/futures-books").json()
    assert report["payload"]["books"] == []
    assert report["components"][0]["reason_code"] == "NO_FUTURES_BOOKS"


def test_a_managed_stop_keeps_the_liquidation_monitor(lane: Lane) -> None:
    """A stopped (not flattened) book still liquidates on a crash bar; it never re-enters."""
    started = lane.client.post("/api/v1/deployments", json=_start_body(lane.strategy.strategy_id))
    assert started.status_code == 201, started.text
    deployment_id = UUID(started.json()["id"])
    snapshot = asyncio.run(lane.execution.get_deployment(deployment_id))
    filled = asyncio.run(_bar(lane, asyncio.run(_bar(lane, snapshot, 30)), 31))
    assert filled.position is not None
    stopped = lane.client.post(f"/api/v1/deployments/{deployment_id}/stop")
    assert stopped.status_code == 200, stopped.text
    crash = Candle(
        starts_at=_OPEN + timedelta(hours=31),
        open=Decimal(130),
        high=Decimal(130),
        low=Decimal(1),
        close=Decimal(2),
        volume=Decimal(10),
    )
    held = asyncio.run(lane.execution.get_deployment(deployment_id))
    binding = asyncio.run(lane.contracts.load_contract(deployment_id))
    state = FuturesBookState(
        deployment_id=deployment_id,
        product_id=_PERP,
        side="long",
        binding=binding,
        margin=_MARGIN,
    )
    with futures_book_scope(state):
        after = asyncio.run(
            maintain_open_inventory(
                held,
                strategy=lane.strategy,
                product=_product(),
                candles=(*_candles(31), crash),
                broker=PaperBroker(),
                store=lane.execution,
            )
        )
    assert after.position is None
    assert after.deployment.status is DeploymentStatus.STOPPED
    assert IntentPurpose.LIQUIDATION in {intent.purpose for intent in after.intents}
    entries = [intent for intent in after.intents if intent.purpose is IntentPurpose.ENTRY]
    assert len(entries) == 1  # the original entry only; a stopped book never re-enters
