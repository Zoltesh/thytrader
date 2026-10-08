"""The execution worker journals every evaluated bar and never lets journaling block trading."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog, candles, paper_book, strategy
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution import decision_journal
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.decision_journal import decision_journal_scope, record_gate_skip
from thytrader.execution.decision_store import (
    DecisionStoreError,
    InMemoryDecisionJournalStore,
)
from thytrader.execution.decisions import DecisionOutcome, DecisionSkipReason
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import (
    _journaled_bar,
    _prune_decisions_when_due,
    _run_cycle,
)
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.trading.models import runtime_from_deployment

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.decisions import BarDecision
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.memory import InMemoryExecutionStore
    from thytrader.trading.models import DeploymentSnapshot


class _FailingJournal(InMemoryDecisionJournalStore):
    """Journal whose writes always fail like an unavailable table."""

    async def upsert(self, decision: BarDecision) -> None:
        """Refuse every write."""
        del decision
        raise DecisionStoreError("Decision journal storage is unavailable.")


class _HangingJournal(InMemoryDecisionJournalStore):
    """Journal whose reads never return, like a stuck connection."""

    async def latest_before(
        self, deployment_id: UUID, product_id: str, bar_starts_at: datetime
    ) -> BarDecision | None:
        """Block forever (the recorder's timeout must cut this off)."""
        del deployment_id, product_id, bar_starts_at
        await asyncio.Event().wait()
        return None


async def _cycle(
    definition: StrategyDefinition,
    store: InMemoryExecutionStore,
    *,
    journal: InMemoryDecisionJournalStore | None,
    audit: InMemoryAuditEventStore,
) -> None:
    """Run one real worker cycle on deterministic demo candles."""
    with execution_audit_scope(audit), decision_journal_scope(journal):
        await _run_cycle(
            store=store,
            publication_store=Catalog(definition),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_store=None,
        )


def _trading_state(snapshot: DeploymentSnapshot) -> tuple[object, ...]:
    """Comparable trading facts of one book (identity-free)."""
    deployment = snapshot.deployment
    return (
        deployment.last_evaluated_bar,
        deployment.last_signal,
        deployment.phase,
        deployment.status,
        deployment.cash,
        tuple((intent.purpose, intent.quantity, intent.price) for intent in snapshot.intents),
        tuple((order.status, order.price) for order in snapshot.orders),
    )


@pytest.mark.anyio
async def test_worker_cycle_journals_the_evaluated_bar() -> None:
    """A real cycle writes exactly one decision for the newest closed bar."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    await _cycle(definition, store, journal=journal, audit=InMemoryAuditEventStore())
    after = await store.get_deployment(snapshot.deployment.id)
    rows = journal.rows()
    assert len(rows) == 1
    assert rows[0].bar_starts_at == after.deployment.last_evaluated_bar
    assert rows[0].outcome in set(DecisionOutcome)
    assert rows[0].rule is not None


@pytest.mark.anyio
async def test_multi_instrument_document_journals_every_covered_product() -> None:
    """Lockstep documents write one row per covered product for the shared bar."""
    single = strategy()
    payload = single.model_dump(mode="python", by_alias=True)
    payload["additional_instruments"] = [
        {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
    ]
    payload["portfolio_limits"]["max_concurrent_positions"] = 2
    definition = type(single).model_validate(payload)
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    await _cycle(definition, store, journal=journal, audit=InMemoryAuditEventStore())
    rows = journal.rows()
    assert sorted(row.product_id for row in rows) == ["BTC-USD", "ETH-USD"]
    assert len({row.bar_starts_at for row in rows}) == 1
    assert {row.deployment_id for row in rows} == {snapshot.deployment.id}


@pytest.mark.anyio
async def test_journal_write_failure_is_audited_and_trading_is_unchanged() -> None:
    """A failing journal is logged and audited; the book advances exactly as without one."""
    definition = strategy()
    control_store, control = await paper_book(definition)
    await _cycle(definition, control_store, journal=None, audit=InMemoryAuditEventStore())
    store, snapshot = await paper_book(definition)
    audit = InMemoryAuditEventStore()
    await _cycle(definition, store, journal=_FailingJournal(), audit=audit)
    journaled = await store.get_deployment(snapshot.deployment.id)
    unjournaled = await control_store.get_deployment(control.deployment.id)
    assert journaled.deployment.last_evaluated_bar is not None
    assert _trading_state(journaled) == _trading_state(unjournaled)
    events = await audit.list_recent(limit=10)
    failures = [event for event in events if event.action == "decision_journal_write_failed"]
    assert len(failures) == 1
    assert "trading continued unchanged" in failures[0].detail
    assert "DecisionStoreError" in failures[0].detail


@pytest.mark.anyio
async def test_hung_journal_is_cut_off_by_the_write_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck journal call times out; the cycle completes and the bar still advances."""
    monkeypatch.setattr(decision_journal, "WRITE_TIMEOUT_SECONDS", 0.05)
    definition = strategy()
    store, snapshot = await paper_book(definition)
    await asyncio.wait_for(
        _cycle(definition, store, journal=_HangingJournal(), audit=InMemoryAuditEventStore()),
        timeout=10,
    )
    after = await store.get_deployment(snapshot.deployment.id)
    assert after.deployment.last_evaluated_bar is not None


@pytest.mark.anyio
async def test_raised_closed_bar_call_is_journaled_as_error_and_reraised() -> None:
    """The original exception propagates unchanged after an error row is written."""
    definition = strategy()
    _store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    window = candles(60)

    async def explode() -> DeploymentSnapshot:
        raise RuntimeError("venue exploded")

    with decision_journal_scope(journal), pytest.raises(RuntimeError, match="venue exploded"):
        await _journaled_bar(
            snapshot,
            strategy=definition,
            product_id="BTC-USD",
            candle=window[-1],
            allow_new_entries=True,
            advance=explode,
        )
    (row,) = journal.rows()
    assert row.outcome is DecisionOutcome.ERROR
    assert row.reason_code == "CYCLE_ERROR"
    assert "RuntimeError" in row.summary


@pytest.mark.anyio
async def test_already_evaluated_bar_is_not_journaled_again() -> None:
    """Between-bar protection on an evaluated bar keeps that bar's real decision."""
    definition = strategy()
    store, snapshot = await paper_book(definition)
    window = candles(60)
    evaluated = replace(snapshot.deployment, last_evaluated_bar=window[-1].starts_at)
    await store.save_deployment(evaluated)
    current = await store.get_deployment(snapshot.deployment.id)
    journal = InMemoryDecisionJournalStore()
    calls: list[str] = []

    async def protect() -> DeploymentSnapshot:
        calls.append("advanced")
        return current

    with decision_journal_scope(journal):
        await _journaled_bar(
            current,
            strategy=definition,
            product_id="BTC-USD",
            candle=window[-1],
            allow_new_entries=False,
            advance=protect,
        )
    assert calls == ["advanced"]
    assert journal.rows() == ()


@pytest.mark.anyio
async def test_a_stale_product_overlay_does_not_reprocess_or_relabel_an_evaluated_bar() -> None:
    """Between-bar maintenance on a single-product book reads the parent's runtime state.

    A product overlay row left one bar behind (as product-scoped reconciliation writes
    one) must not make the evaluated bar look new: no CATCH_UP overwrite, no second pass.
    """
    definition = strategy()
    store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    audit = InMemoryAuditEventStore()
    await _cycle(definition, store, journal=journal, audit=audit)
    evaluated = await store.get_deployment(snapshot.deployment.id)
    (first,) = journal.rows()
    assert first.rule is not None
    bar = evaluated.deployment.last_evaluated_bar
    assert bar is not None
    stale = replace(
        runtime_from_deployment(evaluated.deployment, evaluated.deployment.product_id),
        last_evaluated_bar=bar - timedelta(hours=1),
    )
    await store.save_deployment(
        evaluated.deployment,
        expected_revision=evaluated.deployment.revision,
        instrument_runtime=stale,
    )
    before = await store.get_deployment(snapshot.deployment.id)
    await _cycle(definition, store, journal=journal, audit=audit)
    after = await store.get_deployment(snapshot.deployment.id)
    (row,) = journal.rows()
    assert row.reason_code == first.reason_code
    assert row.rule is not None
    assert _trading_state(after) == _trading_state(before)


@pytest.mark.anyio
async def test_gate_skips_write_each_bar_once_and_never_overwrite_evaluated_bars() -> None:
    """A data gap persisting across cycles writes its bar once; evaluated bars stay."""
    definition = strategy()
    _store, snapshot = await paper_book(definition)
    journal = InMemoryDecisionJournalStore()
    bar = datetime(2026, 3, 9, 12, tzinfo=UTC)
    writes: list[str] = []

    class _CountingJournal(InMemoryDecisionJournalStore):
        async def upsert(self, decision: BarDecision) -> None:
            writes.append(decision.reason_code)
            await journal.upsert(decision)

    counting = _CountingJournal()
    with decision_journal_scope(counting):
        for _ in range(3):
            await record_gate_skip(
                snapshot=snapshot,
                strategy=definition,
                product_ids=("BTC-USD",),
                bar_starts_at=bar,
                reason=DecisionSkipReason.DATA_GAP,
                detail="Market-data window is gapped or missing the latest closed bar.",
            )
        evaluated = replace(
            snapshot, deployment=replace(snapshot.deployment, last_evaluated_bar=bar)
        )
        await record_gate_skip(
            snapshot=evaluated,
            strategy=definition,
            product_ids=("BTC-USD",),
            bar_starts_at=bar,
            reason=DecisionSkipReason.USER_FEED_GATE,
            detail="User-order feed is not connected.",
        )
    assert writes == ["DATA_GAP"]
    (row,) = journal.rows()
    assert row.skip_reason is DecisionSkipReason.DATA_GAP


@pytest.mark.anyio
async def test_retention_runs_at_startup_and_then_every_six_hours() -> None:
    """Pruning is bounded and periodic, not per cycle."""
    journal = InMemoryDecisionJournalStore()
    start = datetime.now(UTC) - timedelta(seconds=1)
    after_first = await _prune_decisions_when_due(journal, start)
    assert after_first > start + timedelta(hours=5)
    assert await _prune_decisions_when_due(journal, after_first) == after_first
    assert await _prune_decisions_when_due(None, start) == start
