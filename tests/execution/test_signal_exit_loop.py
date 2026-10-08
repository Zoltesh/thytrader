"""Signal-based exits in the paper and live closed-bar loop (ADR 0093).

The strategy is the 1h BTC-USD template with ``entry.when`` RSI(14) >= 50 and
``exits.signal_exit`` RSI(14) < 50. A steadily falling window makes the exit rule match
on its newest bar; a rising one leaves it unmatched. Books are inserted already OPEN so
each test controls which bar is the fill bar.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.execution.decision_support import candles, next_candle, product, strategy
from tests.execution.test_live_lifecycle_hardening import _ScriptedVenue, _venue_fill
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.broker import CANCEL_PENDING_REASON, SubmitResult
from thytrader.execution.capital import live_sizing_cash
from thytrader.execution.decision_journal import (
    decision_journal_scope,
    observe_bar,
    record_bar_decision,
)
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import (
    DecisionAction,
    DecisionExitReason,
    DecisionOutcome,
)
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.loop import maintain_open_inventory, process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.execution.decisions import BarDecision
    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.market_data.models import Candle

_QTY = Decimal("0.5")
_RSI_BELOW_50: dict[str, object] = {
    "when": {
        "all": [
            {
                "left": {"indicator": "rsi"},
                "operator": "less_than",
                "right": {"literal": "50"},
            }
        ]
    }
}


def _exit_strategy(*, max_bars_held: int = 96) -> StrategyDefinition:
    """Template strategy with RSI >= 50 entries and an RSI < 50 signal exit."""
    base = strategy(max_bars_held=max_bars_held)
    payload = base.model_dump(mode="python", by_alias=True)
    payload["exits"]["signal_exit"] = _RSI_BELOW_50
    return StrategyDefinition.model_validate(payload)


def _falling() -> tuple[Candle, ...]:
    """Sixty falling 1h bars: RSI(14) is far below 50 on the newest one."""
    return candles(60, step="-1", base="160")


def _rising() -> tuple[Candle, ...]:
    """Sixty rising 1h bars: RSI(14) is far above 50 on the newest one."""
    return candles(60, step="1", base="40")


async def _open_book(
    store: InMemoryExecutionStore,
    definition: StrategyDefinition,
    *,
    mode: DeploymentMode,
    entered_bar: datetime,
    stop_price: str = "1",
    portfolio_id: UUID | None = None,
    allocated_capital: str | None = None,
) -> DeploymentSnapshot:
    """Insert one RUNNING OPEN long book entered at ``entered_bar``."""
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(definition),
        strategy_id=definition.strategy_id,
        product_id="BTC-USD",
        mode=mode,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000") if mode is DeploymentMode.PAPER else None,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        last_evaluated_bar=entered_bar,
        bars_held=0,
        created_at=now,
        updated_at=now,
        timeframe="1h",
        portfolio_id=portfolio_id,
        allocated_capital=None if allocated_capital is None else Decimal(allocated_capital),
        inventory_cost=_QTY * Decimal("100"),
    )
    await store.create_deployment(deployment)
    await store.save_position(
        Position(
            deployment_id=deployment.id,
            quantity=_QTY,
            entry_price=Decimal("100"),
            stop_price=Decimal(stop_price),
            target_price=None,
            entered_bar=entered_bar,
            updated_at=now,
            product_id="BTC-USD",
        ),
        deployment_id=deployment.id,
    )
    return await store.get_deployment(deployment.id)


async def _resting_stop_limit(store: InMemoryExecutionStore, snapshot: DeploymentSnapshot) -> None:
    """Persist the live stop-only protection (ADR 0090) covering the open book."""
    now = utc_now()
    await store.save_order(
        Order(
            id=uuid7(now),
            deployment_id=snapshot.deployment.id,
            intent_id=uuid7(now),
            client_order_id="protect-1",
            side=OrderSide.SELL,
            kind=OrderKind.STOP_LIMIT,
            quantity=_QTY,
            price=Decimal("0.95"),
            stop_trigger_price=Decimal("1"),
            status=OrderStatus.OPEN,
            venue_order_id="stop-1",
            created_at=now,
            updated_at=now,
            product_id="BTC-USD",
        )
    )


async def _journaled(
    snapshot: DeploymentSnapshot,
    *,
    definition: StrategyDefinition,
    window: tuple[Candle, ...],
    store: InMemoryExecutionStore,
    broker: Broker,
) -> tuple[DeploymentSnapshot, BarDecision]:
    """Process one closed bar like the worker and return the bar's journal row."""
    journal = InMemoryDecisionJournalStore()
    with decision_journal_scope(journal), observe_bar() as observations:
        after = await process_closed_bar(
            snapshot,
            strategy=definition,
            product=product(),
            candles=window,
            broker=broker,
            store=store,
            allow_new_entries=False,
        )
        await record_bar_decision(
            strategy=definition,
            product_id="BTC-USD",
            candle=window[-1],
            before=snapshot,
            after=after,
            observations=observations,
            allow_new_entries=False,
        )
    return after, journal.rows()[0]


def _exit_intents(snapshot: DeploymentSnapshot) -> list[IntentPurpose]:
    """Purposes of every non-entry intent the book created."""
    return [
        intent.purpose for intent in snapshot.intents if intent.purpose is not IntentPurpose.ENTRY
    ]


@pytest.mark.anyio
async def test_paper_exit_rule_sells_at_the_close_and_journals_the_rule() -> None:
    """A matched exit rule after the fill bar exits at the close as SIGNAL_EXIT."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    window = _falling()
    snapshot = await _open_book(
        store, definition, mode=DeploymentMode.PAPER, entered_bar=window[-2].starts_at
    )
    after, decision = await _journaled(
        snapshot, definition=definition, window=window, store=store, broker=PaperBroker()
    )
    assert after.position is None
    assert after.deployment.phase is RuntimePhase.FLAT
    assert after.deployment.cooldown_bars_remaining == definition.entry.cooldown_bars
    assert _exit_intents(after) == [IntentPurpose.SIGNAL_EXIT]
    exit_fill = after.fills[-1]
    assert exit_fill.price == window[-1].close
    assert exit_fill.quantity == _QTY
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.SIGNAL
    assert decision.reason_code == "EXIT_SIGNAL"
    assert decision.summary.startswith("Exit (signal): RSI(14)")
    assert decision.exit_rule is not None
    assert decision.exit_rule.outcome is EntryConditionOutcome.MATCHED
    assert decision.exit_rule.condition.result.value == "true"


@pytest.mark.anyio
async def test_paper_exit_rule_is_not_evaluated_on_the_fill_bar() -> None:
    """The bar a position filled on never exits on the signal (same as the backtest)."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    window = _falling()
    snapshot = await _open_book(
        store, definition, mode=DeploymentMode.PAPER, entered_bar=window[-1].starts_at
    )
    after, decision = await _journaled(
        snapshot, definition=definition, window=window, store=store, broker=PaperBroker()
    )
    assert after.position is not None
    assert after.position.signal_exit_bar is None
    assert _exit_intents(after) == []
    assert decision.exit_rule is None


@pytest.mark.anyio
async def test_paper_holding_bar_explains_the_unmet_exit_rule() -> None:
    """An unmatched rule keeps the book and the journal names the deciding leaf."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    window = _rising()
    snapshot = await _open_book(
        store, definition, mode=DeploymentMode.PAPER, entered_bar=window[-2].starts_at
    )
    after, decision = await _journaled(
        snapshot, definition=definition, window=window, store=store, broker=PaperBroker()
    )
    assert after.position is not None
    assert decision.outcome is DecisionOutcome.HOLDING
    assert decision.exit_rule is not None
    assert decision.exit_rule.outcome is EntryConditionOutcome.NOT_MATCHED
    assert "exit rule: RSI(14)" in decision.summary


@pytest.mark.anyio
async def test_paper_protective_stop_wins_a_same_bar_tie() -> None:
    """A bar that trades through the stop exits as a stop even though the rule matched."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    falling = _falling()
    crash = next_candle(falling[-1], close=str(falling[-1].close - 1), low="50")
    window = (*falling[1:], crash)
    snapshot = await _open_book(
        store,
        definition,
        mode=DeploymentMode.PAPER,
        entered_bar=window[-2].starts_at,
        stop_price="60",
    )
    after, decision = await _journaled(
        snapshot, definition=definition, window=window, store=store, broker=PaperBroker()
    )
    assert after.position is None
    assert _exit_intents(after) == [IntentPurpose.STOP]
    assert after.fills[-1].price == Decimal("60")
    assert decision.exit_reason is DecisionExitReason.STOP


@pytest.mark.anyio
async def test_paper_exit_rule_precedes_a_time_exit_due_on_the_same_close() -> None:
    """Both would sell at the close; the declared rule names the exit."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy(max_bars_held=1)
    window = _falling()
    snapshot = await _open_book(
        store, definition, mode=DeploymentMode.PAPER, entered_bar=window[-2].starts_at
    )
    after, _decision = await _journaled(
        snapshot, definition=definition, window=window, store=store, broker=PaperBroker()
    )
    assert _exit_intents(after) == [IntentPurpose.SIGNAL_EXIT]


@pytest.mark.anyio
async def test_live_exit_cancels_protection_before_the_marketable_sell() -> None:
    """Cancel the stop-limit first, then send the cover; nothing re-rests protection."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    window = _falling()
    snapshot = await _open_book(
        store, definition, mode=DeploymentMode.LIVE, entered_bar=window[-2].starts_at
    )
    await _resting_stop_limit(store, snapshot)
    venue = _ScriptedVenue(
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(replace(_venue_fill("marketable-1"), quantity=_QTY),)]},
    )
    after, decision = await _journaled(
        await store.get_deployment(snapshot.deployment.id),
        definition=definition,
        window=window,
        store=store,
        broker=venue,
    )
    assert venue.kinds("cancel") == ["stop-1"]
    assert venue.events.index(("cancel", "stop-1")) < venue.events.index(("place", "marketable-1"))
    assert venue.placed == [OrderKind.MARKETABLE]
    assert after.position is None
    assert after.deployment.phase is RuntimePhase.FLAT
    assert after.deployment.status is DeploymentStatus.RUNNING
    assert after.deployment.mismatch_detail is None
    assert _exit_intents(after) == [IntentPurpose.SIGNAL_EXIT]
    assert decision.exit_reason is DecisionExitReason.SIGNAL
    assert decision.outcome is DecisionOutcome.EXIT


@pytest.mark.anyio
async def test_live_pending_cancel_keeps_exiting_instead_of_re_resting_protection() -> None:
    """Cancel race: an accepted-but-unfinished cancel waits; the next cycle sells, never re-rests.

    Without the durable marker the between-bars cycle would see an unprotected book, rest
    a fresh stop-limit, and the one-bar cross that triggered the exit would be lost.
    """
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    window = _falling()
    snapshot = await _open_book(
        store, definition, mode=DeploymentMode.LIVE, entered_bar=window[-2].starts_at
    )
    await _resting_stop_limit(store, snapshot)
    venue = _ScriptedVenue(
        cancel_results={
            "stop-1": SubmitResult(
                status=OrderStatus.OPEN,
                venue_order_id="stop-1",
                reject_reason=CANCEL_PENDING_REASON,
            )
        },
        statuses={"stop-1": [OrderStatus.OPEN, OrderStatus.CANCELED]},
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(replace(_venue_fill("marketable-1"), quantity=_QTY),)]},
    )
    first, decision = await _journaled(
        await store.get_deployment(snapshot.deployment.id),
        definition=definition,
        window=window,
        store=store,
        broker=venue,
    )
    assert venue.placed == []
    assert first.position is not None
    assert first.position.signal_exit_bar == window[-1].starts_at
    assert first.deployment.status is DeploymentStatus.RUNNING
    assert first.deployment.mismatch_detail is None
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.SIGNAL
    assert decision.action in {DecisionAction.NONE, DecisionAction.ORDER_CANCELED}
    reloaded = await store.get_deployment(snapshot.deployment.id)
    assert reloaded.position is not None
    assert reloaded.position.signal_exit_bar == window[-1].starts_at
    waiting = await maintain_open_inventory(
        reloaded,
        strategy=definition,
        product=product(),
        candles=window,
        broker=venue,
        store=store,
    )
    # Reconciliation now confirms the cancel at the start of this poll, so the
    # cover need not wait for an additional artificial maintenance cycle.
    assert venue.placed == [OrderKind.MARKETABLE]
    assert waiting.position is None
    assert waiting.deployment.status is DeploymentStatus.RUNNING
    settled = await maintain_open_inventory(
        waiting,
        strategy=definition,
        product=product(),
        candles=window,
        broker=venue,
        store=store,
    )
    assert venue.kinds("cancel") == ["stop-1"]
    assert venue.kinds("get").count("stop-1") >= 2
    assert venue.events.index(("get", "stop-1")) < venue.events.index(("place", "marketable-1"))
    assert venue.placed == [OrderKind.MARKETABLE]
    assert OrderKind.STOP_LIMIT not in venue.placed
    assert settled.position is None
    assert settled.deployment.phase is RuntimePhase.FLAT
    assert _exit_intents(settled) == [IntentPurpose.SIGNAL_EXIT]


@pytest.mark.anyio
async def test_live_sleeve_signal_exit_frees_its_allocation() -> None:
    """A portfolio sleeve's sizing cash returns to its full allocation once it is flat."""
    store = InMemoryExecutionStore()
    definition = _exit_strategy()
    window = _falling()
    snapshot = await _open_book(
        store,
        definition,
        mode=DeploymentMode.LIVE,
        entered_bar=window[-2].starts_at,
        portfolio_id=UUID("01985cf0-7b60-7000-8000-0000000000aa"),
        allocated_capital="100",
    )
    assert live_sizing_cash(snapshot.deployment) == Decimal("50")
    venue = _ScriptedVenue(
        place_status={OrderKind.MARKETABLE: OrderStatus.FILLED},
        fills={"marketable-1": [(replace(_venue_fill("marketable-1"), quantity=_QTY),)]},
    )
    after = await process_closed_bar(
        snapshot,
        strategy=definition,
        product=product(),
        candles=window,
        broker=venue,
        store=store,
        allow_new_entries=False,
    )
    assert after.position is None
    assert after.deployment.portfolio_id == snapshot.deployment.portfolio_id
    assert after.deployment.inventory_cost == Decimal(0)
    assert live_sizing_cash(after.deployment) == Decimal("100")
