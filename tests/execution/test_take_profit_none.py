"""take_profit none in paper and live, live stop-only protection, and skip journaling (ADR 0090).

Backtest semantics are covered in ``tests/backtest/test_diagnostics.py``; this module pins
the paper/live halves of the parity contract: no take-profit order, a stop that still
protects the book, and a decision row that names a geometry skip instead of a silent no-op.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.backtest.test_kernel import _bars, _run, _strategy as kernel_strategy
from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.execution.decision_support import (
    candles,
    journaled_bar,
    paper_book,
    strategy as decision_strategy,
)
from tests.execution.test_live_bracket import _candle, _live_open, _product, _RecordingBroker
from thytrader.backtest.kernel import simulate_backtest
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionOutcome, DecisionSkipReason
from thytrader.execution.loop import _runtime_for_admitted_entry, process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.sizing import SizedEntry
from thytrader.market_data.models import Candle
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.trading.protection import (
    ProtectionStatus,
    book_protection_evidence,
    book_protection_status,
)

if TYPE_CHECKING:
    from thytrader.trading.models import DeploymentSnapshot


def _no_tp(strategy: StrategyDefinition) -> StrategyDefinition:
    """Return the strategy with ``take_profit: {"kind": "none"}``."""
    payload = strategy.model_dump(mode="python")
    payload["exits"]["take_profit"] = {"kind": "none"}
    return StrategyDefinition.model_validate(payload)


def _template_no_tp() -> StrategyDefinition:
    """Template strategy (ATR trailing disabled) without a take-profit."""
    return _no_tp(create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC)))


async def _paper_open(
    store: InMemoryExecutionStore, strategy: StrategyDefinition, *, entered_bar: datetime
) -> DeploymentSnapshot:
    """Insert one paper OPEN long book whose position has no target."""
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("9000"),
        phase=RuntimePhase.OPEN,
        last_evaluated_bar=entered_bar,
        bars_held=1,
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    await store.save_position(
        Position(
            deployment_id=deployment.id,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=None,
            entered_bar=entered_bar,
            updated_at=now,
            product_id="BTC-USD",
        ),
        deployment_id=deployment.id,
    )
    return await store.get_deployment(deployment.id)


@pytest.mark.anyio
async def test_paper_book_without_take_profit_rests_nothing_and_stays_protected() -> None:
    """Paper rests no TP order; the synthetic stop still covers the book on closed bars."""
    store = InMemoryExecutionStore()
    strategy = _template_no_tp()
    snapshot = await _paper_open(store, strategy, entered_bar=_candle(1).starts_at)
    quiet = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), _candle(2)),
        broker=PaperBroker(),
        store=store,
        allow_new_entries=False,
    )
    assert quiet.position is not None
    assert quiet.orders == ()
    assert quiet.deployment.phase is RuntimePhase.OPEN
    assert (
        book_protection_status(quiet, product_id="BTC-USD", position=quiet.position)
        is ProtectionStatus.COVERED
    )
    crash = Candle(
        starts_at=_candle(3).starts_at,
        open=Decimal("95"),
        high=Decimal("96"),
        low=Decimal("85"),
        close=Decimal("88"),
        volume=Decimal("10"),
    )
    stopped = await process_closed_bar(
        quiet,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), _candle(2), crash),
        broker=PaperBroker(),
        store=store,
        allow_new_entries=False,
    )
    assert stopped.position is None
    purposes = {intent.purpose for intent in stopped.intents}
    assert IntentPurpose.STOP in purposes
    assert IntentPurpose.TAKE_PROFIT not in purposes


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("side", "stop", "cover", "limit"),
    [
        (PositionSide.LONG, "90", OrderSide.SELL, "85.50"),
        (PositionSide.SHORT, "110", OrderSide.BUY, "115.50"),
    ],
)
async def test_live_book_without_take_profit_rests_one_stop_limit(
    side: PositionSide, stop: str, cover: OrderSide, limit: str
) -> None:
    """Live protects a no-TP book with a venue stop-limit 5% through the stop, never an OCO."""
    store = InMemoryExecutionStore()
    strategy = _template_no_tp()
    broker = _RecordingBroker()
    snapshot = await _live_open(
        store,
        strategy,
        last_evaluated_bar=_candle(1).starts_at,
        entered_bar=_candle(1).starts_at,
        stop_price=Decimal(stop),
        side=side,
    )
    position = snapshot.position
    assert position is not None
    await store.save_position(
        replace(position, target_price=None), deployment_id=position.deployment_id
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    updated = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), _candle(2)),
        broker=broker,
        store=store,
    )
    assert len(broker.placed) == 1
    placed = broker.placed[0]
    assert placed["kind"] is OrderKind.STOP_LIMIT
    assert placed["side"] is cover
    assert placed["stop_trigger_price"] == Decimal(stop)
    assert placed["price"] == Decimal(limit)
    assert placed["take_profit_price"] is None
    assert updated.deployment.phase is RuntimePhase.PENDING_EXIT
    protective = next(intent for intent in updated.intents if intent.kind is OrderKind.STOP_LIMIT)
    assert protective.purpose is IntentPurpose.BRACKET
    assert updated.position is not None
    assert (
        book_protection_status(updated, product_id="BTC-USD", position=updated.position)
        is ProtectionStatus.UNKNOWN
    )
    submitted = book_protection_evidence(updated, product_id="BTC-USD", position=updated.position)
    assert submitted.stop_geometry_valid
    assert submitted.verified_at is None  # Submission alone is not an order-state read.
    observed = await reconcile_open_orders(updated, store=store, broker=broker)
    evidence = book_protection_evidence(observed, product_id="BTC-USD", position=updated.position)
    assert evidence.status is ProtectionStatus.COVERED
    assert evidence.verified_at is not None
    assert evidence.observation_source == "venue_order_state"
    assert evidence.geometry_basis == "stop_limit_trigger"
    again = await process_closed_bar(
        observed,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), _candle(2), _candle(3)),
        broker=broker,
        store=store,
    )
    assert len(broker.placed) == 1
    assert again.position is not None


def test_entry_without_target_never_attaches_a_venue_bracket() -> None:
    """Coinbase attached brackets need a TP limit: a no-TP entry rests stop-only after fill."""
    strategy = _template_no_tp()
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=None,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        pending_target_price=Decimal("150"),
    )
    no_target = SizedEntry(
        quantity=Decimal("0.01"),
        notional=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=None,
    )
    pending, attach = _runtime_for_admitted_entry(
        deployment, sized=no_target, strategy=strategy, is_pyramid_add=False
    )
    assert attach is False
    assert pending.pending_stop_price == Decimal("90")
    assert pending.pending_target_price is None
    with_target = replace(no_target, target_price=Decimal("120"))
    bracketed = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    _pending, attach_with_target = _runtime_for_admitted_entry(
        deployment, sized=with_target, strategy=bracketed, is_pyramid_add=False
    )
    assert attach_with_target is True


@pytest.mark.anyio
async def test_geometry_skip_is_journaled_as_skipped_with_a_precise_reason() -> None:
    """A matched short whose target would be at or below zero writes TARGET_NOT_POSITIVE."""
    base = decision_strategy()
    payload = base.model_dump(mode="python")
    payload["entry"]["side"] = "short"
    payload["exits"]["initial_stop"]["multiple"] = "10"
    payload["exits"]["take_profit"] = {"kind": "reward_risk", "multiple": "10"}
    definition = StrategyDefinition.model_validate(payload)
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    after, decision = await journaled_bar(
        snapshot, definition=definition, window=candles(60), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.reason_code == "TARGET_NOT_POSITIVE"
    assert decision.skip_reason is DecisionSkipReason.ENTRY_GEOMETRY
    assert "take-profit would be at or below zero" in decision.summary
    assert after.orders == ()
    assert after.intents == ()


@pytest.mark.anyio
async def test_backtest_paper_and_live_agree_on_a_no_take_profit_book() -> None:
    """Parity: no target exit in the backtest, no TP order in paper, stop-limit in live."""
    strategy = _template_no_tp()
    long_rows = _bars(
        ("10", "11", "9", "10"),
        ("11", "12", "10", "11"),
        ("14", "15", "12", "14"),
        ("14", "15", "13.5", "14"),
        ("14", "40", "13.9", "39"),
        ("39", "40", "38", "39"),
    )
    research = _research_strategy(strategy)
    result = simulate_backtest(_run(research, evaluation_hours=3), research, long_rows)
    assert result.trades
    assert all(trade.exit.reason != "take_profit" for trade in result.trades)
    paper_store = InMemoryExecutionStore()
    paper = await _paper_open(paper_store, strategy, entered_bar=_candle(1).starts_at)
    paper_after = await process_closed_bar(
        paper,
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), _candle(2)),
        broker=PaperBroker(),
        store=paper_store,
        allow_new_entries=False,
    )
    assert all(intent.purpose is not IntentPurpose.TAKE_PROFIT for intent in paper_after.intents)
    live_store = InMemoryExecutionStore()
    broker = _RecordingBroker()
    live = await _live_open(
        live_store,
        strategy,
        last_evaluated_bar=_candle(1).starts_at,
        entered_bar=_candle(1).starts_at,
    )
    position = live.position
    assert position is not None
    await live_store.save_position(
        replace(position, target_price=None), deployment_id=position.deployment_id
    )
    await process_closed_bar(
        await live_store.get_deployment(live.deployment.id),
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1), _candle(2)),
        broker=broker,
        store=live_store,
    )
    assert [item["kind"] for item in broker.placed] == [OrderKind.STOP_LIMIT]


def _research_strategy(strategy: StrategyDefinition) -> StrategyDefinition:
    """The kernel fixture's 2-bar indicators with this strategy's no-TP exits."""
    payload = kernel_strategy().model_dump(mode="python")
    payload["exits"]["take_profit"] = strategy.exits.take_profit.model_dump(mode="python")
    return StrategyDefinition.model_validate(payload)


@pytest.mark.anyio
async def test_paper_refuses_venue_stop_limit_orders() -> None:
    """Stop-limit protection is live-only; paper enforces stops synthetically."""
    with pytest.raises(ValueError, match="paper does not submit venue stop-limit orders"):
        await PaperBroker().place_order(
            client_order_id="paper-stop",
            product_id="BTC-USD",
            side=OrderSide.SELL,
            kind=OrderKind.STOP_LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("85.5"),
            stop_trigger_price=Decimal("90"),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("side", "direction"),
    [(OrderSide.SELL, "STOP_DIRECTION_STOP_DOWN"), (OrderSide.BUY, "STOP_DIRECTION_STOP_UP")],
)
async def test_coinbase_stop_limit_uses_stop_limit_gtc(side: OrderSide, direction: str) -> None:
    """Live stop-only protection posts stop_limit_stop_limit_gtc with no attached bracket."""
    transport = FakeTransport(
        posts={
            "/api/v3/brokerage/orders": [
                {"success": True, "order": {"order_id": "venue-stop", "status": "PENDING"}}
            ]
        },
        gets={
            "/api/v3/brokerage/orders/historical/venue-stop": [
                {"order": {"order_id": "venue-stop", "status": "OPEN", "filled_size": "0"}}
            ]
        },
    )
    result = await CoinbaseRestBroker(transport).place_order(
        client_order_id="client-stop",
        product_id="BTC-USD",
        side=side,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("85.5"),
        stop_trigger_price=Decimal("90"),
    )
    assert result.status is OrderStatus.OPEN
    body = transport.calls[0][2]
    assert "attached_order_configuration" not in body
    assert body["order_configuration"] == {
        "stop_limit_stop_limit_gtc": {
            "base_size": "0.01",
            "limit_price": "85.5",
            "stop_price": "90",
            "stop_direction": direction,
        }
    }
