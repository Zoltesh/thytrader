"""Each decision outcome as journaled from a real paper closed-bar cycle (ADR 0087)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.execution.decision_support import (
    candles,
    journaled_bar,
    next_candle,
    paper_book,
    strategy,
)
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import (
    ConditionComparisonTrace,
    ConditionGroupTrace,
    ConditionResult,
    DecisionAction,
    DecisionExitReason,
    DecisionOutcome,
    DecisionSkipReason,
)
from thytrader.execution.models import DeploymentStatus, IntentPurpose, PositionSide
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.models import compiled_default_risk_policy


@pytest.mark.anyio
async def test_entry_signal_journals_rule_values_risk_and_the_submitted_order() -> None:
    """A matched rule records leaf values, the allow verdict, and the resting entry order."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    after, decision = await journaled_bar(
        snapshot, definition=definition, window=candles(60), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.ENTRY_SIGNAL
    assert decision.reason_code == "SIGNAL_MATCHED"
    assert decision.action is DecisionAction.ORDER_SUBMITTED
    entry_intent = next(item for item in after.intents if item.purpose is IntentPurpose.ENTRY)
    assert decision.intent_id == entry_intent.id
    assert [order.purpose for order in decision.orders] == [IntentPurpose.ENTRY]
    assert decision.order_ids == tuple(order.order_id for order in decision.orders)
    assert decision.risk is not None
    assert (decision.risk.decision, decision.risk.reason_code) == ("allow", "ALLOWED")
    assert decision.rule is not None
    assert decision.rule.outcome is EntryConditionOutcome.MATCHED
    entry = decision.rule.entry
    assert isinstance(entry, ConditionGroupTrace)
    leaf = entry.children[0]
    assert isinstance(leaf, ConditionComparisonTrace)
    assert (leaf.label, leaf.result, leaf.left.value, leaf.right.value) == (
        "RSI(14) ≥ 50",
        ConditionResult.TRUE,
        "100",
        "50",
    )
    assert decision.rule.signal is not None
    assert [item.indicator_id for item in decision.rule.signal.indicator_values] == [
        "fast",
        "slow",
        "rsi",
        "atr",
    ]
    assert decision.close_price == "159"
    assert decision.position is None
    assert decision.summary.startswith("Entry: RSI(14) 100 ≥ 50 → buy ")


@pytest.mark.anyio
async def test_no_signal_names_the_unmet_leaf_with_its_value_and_threshold() -> None:
    """A falling market reads ``No trade: RSI(14) 0 needs ≥ 50``."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    after, decision = await journaled_bar(
        snapshot,
        definition=definition,
        window=candles(60, step="-1", base="200"),
        store=store,
        journal=journal,
    )
    assert decision.outcome is DecisionOutcome.NO_SIGNAL
    assert decision.reason_code == "CONDITIONS_NOT_MET"
    assert decision.action is DecisionAction.NONE
    assert decision.summary == "No trade: RSI(14) 0 needs ≥ 50"
    assert decision.rule is not None
    assert decision.rule.entry.result is ConditionResult.FALSE
    assert after.intents == ()


@pytest.mark.anyio
async def test_offset_breakout_journals_the_lagged_level_the_runtime_compared() -> None:
    """``Close > Highest(3, high) (1 bar ago)`` records the prior bar's level, not this bar's.

    On a steady rise the current 3-bar high (159.5) is above the close (159), so only
    the one-bar-lagged level (158.5) lets the breakout match.
    """
    definition = strategy(
        when={
            "all": [
                {
                    "left": {"indicator": "close_now"},
                    "operator": "greater_than",
                    "right": {"indicator": "prior_high"},
                }
            ]
        },
        extra_indicators=(
            {"id": "close_now", "kind": "identity", "input": "close", "parameters": {}},
            {
                "id": "prior_high",
                "kind": "highest",
                "input": "high",
                "parameters": {"period": 3},
                "offset": 1,
            },
        ),
    )
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    _, decision = await journaled_bar(
        snapshot, definition=definition, window=candles(60), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.ENTRY_SIGNAL
    assert decision.rule is not None
    entry = decision.rule.entry
    assert isinstance(entry, ConditionGroupTrace)
    leaf = entry.children[0]
    assert isinstance(leaf, ConditionComparisonTrace)
    assert leaf.label == "Close > Highest(3, high) (1 bar ago)"
    assert (leaf.left.value, leaf.right.value) == ("159", "158.5")
    assert decision.summary.startswith("Entry: Close 159 > Highest(3, high) (1 bar ago) 158.5")


@pytest.mark.anyio
async def test_holding_records_the_filled_entry_and_end_of_bar_position() -> None:
    """The bar that fills a resting entry holds the new book and links the entry fill."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    window = candles(60)
    after, _entry = await journaled_bar(
        snapshot, definition=definition, window=window, store=store, journal=journal
    )
    fill_bar = next_candle(window[-1], close="160", low="157")
    held, decision = await journaled_bar(
        after, definition=definition, window=(*window, fill_bar), store=store, journal=journal
    )
    assert held.position is not None
    assert decision.outcome is DecisionOutcome.HOLDING
    assert decision.position is not None
    assert decision.position.side is PositionSide.LONG
    assert decision.position.entry_price == "159"
    assert [fill.purpose for fill in decision.fills] == [IntentPurpose.ENTRY]
    assert "entry filled @ 159" in decision.summary


@pytest.mark.anyio
async def test_exit_on_a_paper_stop_names_the_stop_and_the_fill() -> None:
    """A bar trading through the stop exits marketably and journals ``exit (stop)``."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    window = candles(60)
    after, _entry = await journaled_bar(
        snapshot, definition=definition, window=window, store=store, journal=journal
    )
    fill_bar = next_candle(window[-1], close="160", low="157")
    held, _holding = await journaled_bar(
        after, definition=definition, window=(*window, fill_bar), store=store, journal=journal
    )
    assert held.position is not None
    stop = held.position.stop_price
    stop_bar = next_candle(fill_bar, close=str(stop - 1), low=str(stop - 2))
    exited, decision = await journaled_bar(
        held,
        definition=definition,
        window=(*window, fill_bar, stop_bar),
        store=store,
        journal=journal,
    )
    assert exited.position is None
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.STOP
    assert decision.reason_code == "EXIT_STOP"
    assert decision.action is DecisionAction.ORDER_SUBMITTED
    assert any(fill.purpose is IntentPurpose.STOP for fill in decision.fills)
    assert decision.summary.startswith("Exit (stop): sell ")
    assert decision.position is None


@pytest.mark.anyio
async def test_exit_on_a_filled_take_profit_names_the_target() -> None:
    """A resting take-profit traded through on the bar journals ``exit (target)``."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    window = candles(60)
    after, _entry = await journaled_bar(
        snapshot, definition=definition, window=window, store=store, journal=journal
    )
    fill_bar = next_candle(window[-1], close="160", low="157")
    held, _holding = await journaled_bar(
        after, definition=definition, window=(*window, fill_bar), store=store, journal=journal
    )
    assert held.position is not None
    target = held.position.target_price
    assert target is not None
    target_bar = next_candle(fill_bar, close=str(target), high=str(target + 1), low="159")
    exited, decision = await journaled_bar(
        held,
        definition=definition,
        window=(*window, fill_bar, target_bar),
        store=store,
        journal=journal,
    )
    assert exited.position is None
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.TARGET
    assert decision.action is DecisionAction.NONE
    assert any(fill.purpose is IntentPurpose.TAKE_PROFIT for fill in decision.fills)


@pytest.mark.anyio
async def test_time_exit_is_journaled_as_exit_time() -> None:
    """Reaching ``max_bars_held`` exits marketably as ``exit (time)``."""
    definition = strategy(max_bars_held=1)
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    window = candles(60)
    after, _entry = await journaled_bar(
        snapshot, definition=definition, window=window, store=store, journal=journal
    )
    fill_bar = next_candle(window[-1], close="160", low="157")
    held, _holding = await journaled_bar(
        after, definition=definition, window=(*window, fill_bar), store=store, journal=journal
    )
    quiet = next_candle(fill_bar, close="161", low="159.5", high="161.5")
    _exited, decision = await journaled_bar(
        held, definition=definition, window=(*window, fill_bar, quiet), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.TIME
    assert decision.reason_code == "EXIT_TIME"


@pytest.mark.anyio
async def test_entry_blocked_carries_the_risk_verdict_code_and_message() -> None:
    """A policy that does not allow the product blocks the matched entry."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    policy = compiled_default_risk_policy().model_copy(update={"product_allowlist": ("ETH-USD",)})
    after, decision = await journaled_bar(
        snapshot,
        definition=definition,
        window=candles(60),
        store=store,
        journal=journal,
        risk_policy=policy,
    )
    assert decision.outcome is DecisionOutcome.ENTRY_BLOCKED
    assert decision.reason_code == "PRODUCT_NOT_ALLOWLISTED"
    assert decision.risk is not None
    assert decision.risk.decision == "deny"
    assert decision.summary.startswith("Blocked: PRODUCT_NOT_ALLOWLISTED — ")
    assert decision.rule is not None
    assert decision.rule.outcome is EntryConditionOutcome.MATCHED
    assert after.intents == ()


@pytest.mark.anyio
async def test_cooldown_bar_is_skipped_with_bars_left() -> None:
    """A book in cooldown does not evaluate the rule and says how many bars remain."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    await store.save_deployment(replace(snapshot.deployment, cooldown_bars_remaining=3))
    cooling = await store.get_deployment(snapshot.deployment.id)
    journal = InMemoryDecisionJournalStore()
    _after, decision = await journaled_bar(
        cooling, definition=definition, window=candles(60), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.COOLDOWN
    assert decision.reason_code == "COOLDOWN"
    assert decision.summary == "Skipped: cooldown (2 bar(s) left after the last exit)"
    assert decision.rule is None


@pytest.mark.anyio
async def test_warmup_bar_is_skipped_naming_the_undefined_indicator() -> None:
    """Too few closed bars leave RSI undefined: unknown, never ``conditions not met``."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    _after, decision = await journaled_bar(
        snapshot, definition=definition, window=candles(5), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.WARMUP
    assert decision.summary == "Skipped: warming up — RSI(14) has no value yet"
    assert decision.rule is not None
    assert decision.rule.entry.result is ConditionResult.UNKNOWN


@pytest.mark.anyio
async def test_paused_flat_book_is_skipped_with_the_pause_detail() -> None:
    """A paused flat book manages nothing, evaluates nothing, and names the pause."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    await store.save_deployment(
        replace(
            snapshot.deployment,
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Paused by the operator.",
        )
    )
    paused = await store.get_deployment(snapshot.deployment.id)
    journal = InMemoryDecisionJournalStore()
    _after, decision = await journaled_bar(
        paused, definition=definition, window=candles(60), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.PAUSED
    assert decision.summary == "Skipped: paused — Paused by the operator."


@pytest.mark.anyio
async def test_catch_up_bar_after_downtime_is_skipped() -> None:
    """Older recovered bars never enter; the journal says so instead of no-signal."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    after, decision = await journaled_bar(
        snapshot,
        definition=definition,
        window=candles(60),
        store=store,
        journal=journal,
        allow_new_entries=False,
    )
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.CATCH_UP
    assert after.intents == ()


@pytest.mark.anyio
async def test_working_entry_bar_is_skipped_as_pending_entry() -> None:
    """While the resting entry waits, the next bar is a pending-entry skip."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    window = candles(60)
    after, _entry = await journaled_bar(
        snapshot, definition=definition, window=window, store=store, journal=journal
    )
    above = next_candle(window[-1], close="162", low="160", high="163")
    _waiting, decision = await journaled_bar(
        after, definition=definition, window=(*window, above), store=store, journal=journal
    )
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.PENDING_ENTRY
    assert decision.summary == "Waiting: entry order working (1 of 2 bars)"


@pytest.mark.anyio
async def test_signal_evaluation_error_is_journaled_as_error() -> None:
    """A fail-closed evaluation error pauses the book and journals ``error``."""
    draft = strategy()
    payload = draft.model_dump(mode="python", by_alias=True)
    payload["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {"warmup_bars": 2, "required_fields": ["close"]},
        "indicators": [
            {"id": "htf_sma", "kind": "sma", "input": "close", "parameters": {"period": 2}}
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "greater_than",
                    "right": {"literal": "0"},
                }
            ]
        },
    }
    definition = type(draft).model_validate(payload)
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    after, decision = await journaled_bar(
        snapshot, definition=definition, window=candles(60), store=store, journal=journal
    )
    assert after.deployment.status is DeploymentStatus.PAUSED
    assert decision.outcome is DecisionOutcome.ERROR
    assert decision.reason_code == "EVALUATION_ERROR"
    assert decision.summary.startswith("Error: HTF candles are required")
