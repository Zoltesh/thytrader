"""Why-trade records freeze at intent persist for strategy and discretionary books."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.execution.test_loop import _always_entry_strategy, _candles, _product, _running_snapshot
from thytrader.execution.discretionary import parse_discretionary_request, place_discretionary_order
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.paper import PaperBroker
from thytrader.execution.trade_reason_scope import (
    strategy_trade_reason_scope,
    trade_reason_scope,
)
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.memory.recording import compose_trade_reasons
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.memory.trade_reasons import TradeReasonOrigin, TradeReasonSignalKind
from thytrader.risk.models import compiled_default_risk_policy


@pytest.mark.anyio
async def test_strategy_entry_records_published_identity_and_risk() -> None:
    """A closed-bar strategy entry freezes strategy version and the risk verdict."""
    store = InMemoryExecutionStore()
    memory = InMemoryExperientialMemoryStore()
    strategy = _always_entry_strategy()
    snapshot = await _running_snapshot(store, strategy)
    policy = compiled_default_risk_policy()
    warmup = _candles(30, low_offset=Decimal("0.01"))
    with trade_reason_scope(
        strategy_trade_reason_scope(
            memory,
            deployment=snapshot.deployment,
            strategy=strategy,
            policy=policy,
        )
    ):
        first = await process_closed_bar(
            snapshot,
            strategy=strategy,
            product=_product(),
            candles=warmup,
            broker=PaperBroker(),
            store=store,
            risk_policy=policy,
        )
    assert first.intents
    rows = await memory.list_trade_reasons()
    assert len(rows) == 1
    record = rows[0]
    assert record.origin is TradeReasonOrigin.RUNTIME
    assert record.signal.kind is TradeReasonSignalKind.STRATEGY_ENTRY
    assert record.strategy is not None
    assert record.strategy.version == strategy.version
    assert record.strategy.strategy_id == strategy.strategy_id
    assert record.risk.decision == "allow"
    composed = await compose_trade_reasons(rows, store)
    assert composed[0].reconcile.ledger_available is True
    assert composed[0].reconcile.order_id is not None


@pytest.mark.anyio
async def test_discretionary_place_records_note_and_omits_strategy() -> None:
    """An on-demand note is the first attributed why-note; no published strategy."""
    store = InMemoryExecutionStore()
    memory = InMemoryExperientialMemoryStore()
    request = parse_discretionary_request(
        mode="paper",
        product_id="BTC-USD",
        entry_kind="marketable",
        stop_price="50000",
        take_profit_price="200000",
        origin="human",
        idempotency_key="why-1",
        timeframe="5m",
        quantity="0.01",
        paper_starting_cash="10000",
        note="Manual fade of the open.",
    )
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=request,
        live_allowed=False,
        memory_store=memory,
    )
    assert snapshot.intents
    rows = await memory.list_trade_reasons()
    assert rows
    record = next(item for item in rows if item.purpose == "entry")
    assert record.origin is TradeReasonOrigin.HUMAN
    assert record.deployment_kind == "discretionary"
    assert record.strategy is None
    assert record.signal.kind is TradeReasonSignalKind.DISCRETIONARY
    assert record.notes[0].body == "Manual fade of the open."
    assert record.notes[0].origin.value == "human"
    assert all(item.strategy is None for item in rows)
    composed = await compose_trade_reasons((record,), store)
    assert composed[0].reconcile.ledger_available is True


@pytest.mark.anyio
async def test_unscoped_submit_does_not_invent_a_why_row() -> None:
    """Without a bound trade-reason scope, persist stays order-path only."""
    store = InMemoryExecutionStore()
    memory = InMemoryExperientialMemoryStore()
    strategy = _always_entry_strategy()
    snapshot = await _running_snapshot(store, strategy)
    await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_candles(30, low_offset=Decimal("0.01")),
        broker=PaperBroker(),
        store=store,
    )
    assert await memory.list_trade_reasons() == ()
